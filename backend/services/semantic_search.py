"""Semantic product + knowledge-base search using OpenAI embeddings + MongoDB Atlas Vector Search.

On first catalog sync (or explicit call), this embeds every product's title +
description using text-embedding-3-small and stores the 1536-dim vector in the
`products` collection.

At query time, the customer's message is embedded and a `$vectorSearch` aggregation
finds the semantically closest products — even when no exact keyword matches exist.

Falls back to returning an empty list if:
  - OPENAI_API_KEY is not set
  - The Atlas vector search index does not exist yet
  - Any other error occurs

The caller (ai_tools.py search_products) handles the fallback to regex search.

Audit fix C-2: Knowledge base documents are embedded the same way using `kb_embedding_index`.
This replaces the broken re.escape() exact-match approach in search_knowledge_base.

SETUP (one-time, done in Atlas UI):
  Products:
    Collection: products
    Index name: product_embedding_index
    Field: embedding
    Dimensions: 1536
    Similarity: cosine

  Knowledge base:
    Collection: knowledge_documents
    Index name: kb_embedding_index
    Field: embedding
    Dimensions: 1536
    Similarity: cosine
"""
import logging
import os
from typing import Optional

from bson import ObjectId

from database import db

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
VECTOR_INDEX_NAME = "product_embedding_index"
KB_VECTOR_INDEX_NAME = "kb_embedding_index"  # C-2 audit fix: separate index for knowledge_documents


def _make_product_text(product: dict) -> str:
    """Build the text to embed for a product. Include title, description, tags."""
    parts = []
    title = (product.get("title") or "").strip()
    if title:
        parts.append(title)
    desc = (product.get("description") or product.get("body_html") or "").strip()
    if desc:
        # Truncate long HTML descriptions
        import re
        clean = re.sub(r"<[^>]+>", " ", desc)
        clean = re.sub(r"\s+", " ", clean).strip()
        parts.append(clean[:300])
    tags = product.get("tags") or []
    if isinstance(tags, list):
        parts.append(" ".join(str(t) for t in tags))
    elif isinstance(tags, str):
        parts.append(tags)
    category = product.get("category") or product.get("product_type") or ""
    if category:
        parts.append(str(category))
        
    attr_vals = product.get("available_attribute_values") or {}
    for attr_name, vals in attr_vals.items():
        if isinstance(vals, list) and vals:
            parts.append(f"{attr_name.replace('_', ' ').title()}: " + ", ".join(str(v) for v in vals))
            
    return " | ".join(parts)[:1000]


async def _get_openai_client():
    """Return an AsyncOpenAI client if OPENAI_API_KEY is set."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=api_key)
    except ImportError:
        logger.warning("openai package not installed — semantic search unavailable")
        return None


async def embed_text(text: str) -> Optional[list[float]]:
    """Return a 1536-dim embedding vector for a text string."""
    client = await _get_openai_client()
    if not client or not text.strip():
        return None
    try:
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text.strip(),
        )
        return response.data[0].embedding
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return None


def _text_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(f"{EMBEDDING_MODEL}|{text}".encode("utf-8")).hexdigest()[:32]


async def embed_products(merchant_id: str, force: bool = False) -> dict:
    """Embed all products for a merchant whose embedding is missing or stale.

    A product is stale when the text it was embedded from (title, description,
    tags, category) has changed since; tracked via ``embedding_text_hash``.

    Args:
        merchant_id: The merchant's ObjectId string.
        force: Re-embed all products even if they already have embeddings.

    Returns:
        {"embedded": int, "skipped": int, "errors": int}
    """
    client = await _get_openai_client()
    if not client:
        return {"embedded": 0, "skipped": 0, "errors": 0, "note": "OPENAI_API_KEY not set"}

    oid = ObjectId(merchant_id)
    candidates = await db.products.find(
        {"merchant_id": oid},
        {"_id": 1, "title": 1, "description": 1, "body_html": 1, "tags": 1,
         "category": 1, "product_type": 1, "embedding_text_hash": 1,
         "has_embedding": {"$cond": [{"$ifNull": ["$embedding", False]}, True, False]}},
    ).to_list(None)

    products = []
    skipped = 0
    for p in candidates:
        text = _make_product_text(p)
        if not text.strip():
            skipped += 1
            continue
        h = _text_hash(text)
        if force or not p.get("has_embedding") or p.get("embedding_text_hash") != h:
            products.append((p, text, h))
        else:
            skipped += 1

    embedded = errors = 0
    # Batch embed in groups of 20 to avoid rate limits
    batch_size = 20
    for i in range(0, len(products), batch_size):
        batch = products[i:i + batch_size]
        texts = [t for _, t, _ in batch]
        try:
            response = await client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            for (product, _t, h), emb_obj in zip(batch, response.data):
                await db.products.update_one(
                    {"_id": product["_id"]},
                    {"$set": {"embedding": emb_obj.embedding, "embedding_model": EMBEDDING_MODEL,
                              "embedding_text_hash": h}}
                )
                embedded += 1
        except Exception as exc:
            logger.error("Batch embedding error (batch starting at %d): %s", i, exc)
            errors += len(batch)

    logger.info(
        "Embedding complete for merchant %s: %d embedded, %d skipped, %d errors",
        merchant_id, embedded, skipped, errors,
    )
    return {"embedded": embedded, "skipped": skipped, "errors": errors}


async def embedding_coverage(merchant_id: Optional[str] = None) -> dict:
    """Return {total, embedded, ratio} for products (optionally for one merchant)."""
    q: dict = {}
    if merchant_id:
        q["merchant_id"] = ObjectId(merchant_id)
    total = await db.products.count_documents(q)
    embedded = await db.products.count_documents({**q, "embedding": {"$exists": True}})
    return {"total": total, "embedded": embedded,
            "ratio": round(embedded / total, 4) if total else 1.0}


_embedding_running: set = set()
_embedding_rerun: set = set()


def schedule_embedding(merchant_id: str) -> None:
    """Best-effort background (re-)embedding for a merchant's catalogue.

    Concurrent requests for the same merchant are coalesced: at most one pass
    runs at a time, and a single follow-up pass is queued if more changes arrive.
    """
    mid = str(merchant_id)
    if mid in _embedding_running:
        _embedding_rerun.add(mid)
        return
    try:
        import asyncio

        async def _run():
            _embedding_running.add(mid)
            try:
                while True:
                    _embedding_rerun.discard(mid)
                    try:
                        await embed_products(mid)
                    except Exception as exc:  # pragma: no cover - best effort
                        logger.warning("Background embedding failed for %s: %s", mid, exc)
                    if mid not in _embedding_rerun:
                        break
            finally:
                _embedding_running.discard(mid)

        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        logger.debug("No running loop; embedding not scheduled for %s", mid)


async def semantic_search(merchant_id: str, query: str, limit: int = 5, threshold: float = 0.55) -> list[dict]:
    """Find products semantically matching the query using Atlas Vector Search.

    Returns a list of product dicts (same shape as regular product docs).
    Returns an empty list on any error or if vector index is not configured.
    """
    query_embedding = await embed_text(query)
    if not query_embedding:
        return []

    oid = ObjectId(merchant_id)
    try:
        # Phase 3: Intelligent Query Routing
        merchant = await db.merchants.find_one({"_id": oid}, {"vector_index_status": 1})
        is_syncing = merchant and merchant.get("vector_index_status") == "SYNCING"

        if is_syncing:
            logger.info(f"Merchant {merchant_id} vector index is SYNCING. Falling back to regex match.")
            regex_pattern = {"$regex": query.replace(" ", ".*"), "$options": "i"}
            pipeline = [
                {
                    "$match": {
                        "merchant_id": oid,
                        "$or": [
                            {"title": regex_pattern},
                            {"tags": regex_pattern}
                        ]
                    }
                }
            ]
        else:
            pipeline = [
                {
                    "$vectorSearch": {
                        "index": VECTOR_INDEX_NAME,
                        "path": "embedding",
                        "queryVector": query_embedding,
                        "numCandidates": limit * 10,
                        "limit": limit,
                        "filter": {
                            "$and": [
                                {"merchant_id": {"$eq": oid}},
                                {"status": {"$in": ["active", "published"]}}
                            ]
                        },
                    }
                }
            ]
            
        pipeline.append({
            "$project": {
                "_id": 1, "title": 1, "price": 1, "status": 1,
                "url": 1, "image_url": 1, "category": 1,
                "score": {"$meta": "vectorSearchScore"} if not is_syncing else 1.0,
            }
        })
        results = await db.products.aggregate(pipeline).to_list(limit)
        # Filter by minimum relevance score
        relevant = [r for r in results if r.get("score", 0) >= threshold]
        logger.info(
            "Semantic search '%s' \u2192 %d results (from %d candidates)",
            query[:60], len(relevant), len(results)
        )
        return relevant
    except Exception as exc:
        # Most likely: vector index not created yet in Atlas
        if "index" in str(exc).lower() or "vectorSearch" in str(exc):
            logger.debug(
                "Atlas vector search index not available \u2014 falling back to in-memory cosine similarity. "
                "Create index '%s' in Atlas UI to enable native semantic search.",
                VECTOR_INDEX_NAME,
            )
            try:
                # In-memory Fallback for Free Tier / Missing Index
                import math
                
                # Fetch all embeddings for this merchant
                candidates = await db.products.find(
                    {
                        "merchant_id": oid,
                        "status": {"$in": ["active", "published"]},
                        "embedding": {"$exists": True}
                    },
                    {"_id": 1, "title": 1, "price": 1, "status": 1, "url": 1, "image_url": 1, "category": 1, "embedding": 1}
                ).to_list(None)
                
                # Normalize query vector
                query_norm = math.sqrt(sum(x * x for x in query_embedding))
                if query_norm == 0:
                    return []
                    
                scored_candidates = []
                for p in candidates:
                    emb = p.get("embedding")
                    if not emb or len(emb) != len(query_embedding):
                        continue
                        
                    # Calculate Cosine Similarity
                    dot_product = sum(a * b for a, b in zip(query_embedding, emb))
                    emb_norm = math.sqrt(sum(x * x for x in emb))
                    if emb_norm == 0:
                        continue
                        
                    score = dot_product / (query_norm * emb_norm)
                    
                    p["score"] = score
                    scored_candidates.append(p)
                        
                scored_candidates.sort(key=lambda x: x["score"], reverse=True)
                relevant = [r for r in scored_candidates if r.get("score", 0) >= threshold][:limit]
                logger.info("In-Memory Semantic search '%s' -> %d results (from %d candidates)", query[:60], len(relevant), len(candidates))
                return relevant
            except Exception as inner_exc:
                logger.warning("In-memory Semantic search fallback error: %s", inner_exc)
                return []
        else:
            logger.warning("Semantic search error: %s", exc)
        return []


# ── C-2 Audit fix: Semantic knowledge-base search ────────────────────────────

def _make_kb_text(doc: dict) -> str:
    """Build the text to embed for a knowledge-base document.

    Includes title, category, and full content so the vector captures the
    semantic meaning of the policy/FAQ answer.
    Product catalog documents can be 1800+ chars with all variant data,
    so we allow up to 2500 chars total to avoid losing variant info.
    """
    parts = []
    title = (doc.get("title") or "").strip()
    if title:
        parts.append(title)
    category = (doc.get("category") or "").strip()
    if category:
        parts.append(category)
    content = (doc.get("content") or "").strip()
    if content:
        parts.append(content[:2000])
    return " | ".join(parts)[:2500]


async def embed_knowledge_documents(merchant_id: str, force: bool = False) -> dict:
    """Embed all knowledge base documents for a merchant that don't yet have an embedding.

    Call this whenever a merchant adds or updates a knowledge document.
    The kb_embedding_index in Atlas must be created (same config as product index,
    but on the knowledge_documents collection).

    Args:
        merchant_id: The merchant's ObjectId string.
        force: Re-embed all docs even if they already have embeddings.

    Returns:
        {"embedded": int, "skipped": int, "errors": int}
    """
    client = await _get_openai_client()
    if not client:
        return {"embedded": 0, "skipped": 0, "errors": 0, "note": "OPENAI_API_KEY not set"}

    oid = ObjectId(merchant_id)
    query = {"merchant_id": oid}
    if not force:
        query["embedding"] = {"$exists": False}

    docs = await db.knowledge_documents.find(
        query, {"_id": 1, "title": 1, "category": 1, "content": 1}
    ).to_list(None)

    embedded = skipped = errors = 0
    batch_size = 20
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i + batch_size]
        texts = [_make_kb_text(d) for d in batch]
        try:
            response = await client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            for doc, emb_obj in zip(batch, response.data):
                await db.knowledge_documents.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"embedding": emb_obj.embedding, "embedding_model": EMBEDDING_MODEL}}
                )
                embedded += 1
        except Exception as exc:
            logger.error("KB batch embedding error (batch starting at %d): %s", i, exc)
            errors += len(batch)

    logger.info(
        "KB embedding complete for merchant %s: %d embedded, %d skipped, %d errors",
        merchant_id, embedded, skipped, errors,
    )
    return {"embedded": embedded, "skipped": skipped, "errors": errors}


async def semantic_kb_search(merchant_id: str, query: str, limit: int = 5, product_id: Optional[str] = None) -> list[dict]:
    """Find knowledge base documents semantically matching the customer's query.

    Audit fix C-2: replaces the broken re.escape() exact-match approach.
    - Handles Roman Urdu queries that don't lexically match English policy document titles
    - Handles natural language questions ("size guide chahiye", "wash kaise karein")
    - Handles concept-level queries ("bundles" → finds "Pack of 2", "Pack of 3")
    - Returns documents ordered by relevance score
    - Falls back to empty list if Atlas index not configured (caller then uses regex)

    Args:
        merchant_id: The merchant's ObjectId string.
        query: The customer's natural language question.
        limit: Maximum number of documents to return.

    Returns:
        List of KB document dicts with title, category, content fields.
    """
    query_embedding = await embed_text(query)
    if not query_embedding:
        return []

    oid = ObjectId(merchant_id)
    try:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": KB_VECTOR_INDEX_NAME,
                    "path": "embedding",
                    "queryVector": query_embedding,
                    "numCandidates": max(limit * 20, 100),
                    "limit": limit,
                    "filter": {
                        "$and": [
                            {"merchant_id": {"$eq": oid}},
                            {"active": {"$ne": False}} # Assume true if missing
                        ]
                    }
                }
            }
        ]
        
        if product_id:
            try:
                pid = ObjectId(product_id)
                pipeline[0]["$vectorSearch"]["filter"]["$and"].append({"product_id": {"$eq": pid}})
            except Exception:
                pass
                
        pipeline.extend([
            {
                "$project": {
                    "_id": 1, "title": 1, "category": 1, "content": 1, "active": 1, "updated_at": 1, "authority_level": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            }
        ])
        
        print("SEMANTIC_SEARCH_PIPELINE:", pipeline)
        
        results = await db.knowledge_documents.aggregate(pipeline).to_list(limit)
        # Lower threshold to 0.40 to catch concept-level matches (e.g. "bundles" → "pack")
        relevant = [r for r in results if r.get("score", 0) >= 0.40]
        
        print("RELEVANT DOCS:")
        for r in relevant:
            print(f"ID: {r['_id']}, ProductID: {r.get('product_id', 'MISSING')}, Title: {r.get('title')}")
            
        logger.info(
            "KB semantic search '%s' \u2192 %d results",
            query[:60], len(relevant),
        )
        return relevant
    except Exception as exc:
        if "index" in str(exc).lower() or "vectorSearch" in str(exc):
            logger.debug(
                "Atlas KB vector search index not available \u2014 falling back to regex. "
                "Create index '%s' on knowledge_documents collection in Atlas UI.",
                KB_VECTOR_INDEX_NAME,
            )
        else:
            logger.warning("KB semantic search error: %s", exc)
        return []

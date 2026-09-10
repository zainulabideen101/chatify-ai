"""Controlled AI tools: OpenAI-style function schemas + deterministic, tenant-scoped executors.

The LLM may REQUEST any of these; the backend executes them against the merchant's
own data only. No external mutation happens without a live connection.
"""
import re
from bson import ObjectId
from database import db, serialize
from services.commerce import address_validator

# ---- Tool schemas exposed to the LLM (OpenAI function-calling format) ----
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_products",
        "description": "Search the store catalog by keyword (product name/type). Returns matching products with price and status.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "search text, e.g. 'black hoodie'"}
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "check_variant_availability",
        "description": "Check current inventory, price and availability of a product variant by size/colour. Use before confirming availability or price.",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string", "description": "product name"},
            "size": {"type": "string"},
            "color": {"type": "string"}
        }, "required": ["product"]}}},
    {"type": "function", "function": {
        "name": "find_product_media",
        "description": "Find a real photo or video for a product. tag examples: real_photo, wearing_video, product_video, fabric_closeup.",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string"},
            "tag": {"type": "string", "description": "media tag/type requested"}
        }, "required": ["product"]}}},
    {"type": "function", "function": {
        "name": "recommend_related",
        "description": "Get merchant-defined cross-sell/upsell products related to a product.",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string"},
            "kind": {"type": "string", "enum": ["cross_sell", "upsell"]}
        }, "required": ["product"]}}},
    {"type": "function", "function": {
        "name": "validate_address",
        "description": "Validate whether a delivery address is complete/deliverable. Returns missing fields if incomplete.",
        "parameters": {"type": "object", "properties": {
            "address": {"type": "string"}, "city": {"type": "string"}
        }, "required": ["address"]}}},
    {"type": "function", "function": {
        "name": "get_shipping_estimate",
        "description": "Get delivery cost and estimated days for a city.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "search_knowledge_base",
        "description": "Retrieve the merchant's real policy/FAQ text (shipping, returns, warranty, size guide) before answering factual policy questions.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "build_order_summary",
        "description": "Compute an order summary with subtotal, shipping and total. Does NOT create the order — used to show the customer a summary before confirmation.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "product": {"type": "string"}, "size": {"type": "string"}, "quantity": {"type": "integer"}}}},
            "city": {"type": "string"}, "payment_method": {"type": "string"}
        }, "required": ["items"]}}},
    {"type": "function", "function": {
        "name": "create_shopify_order",
        "description": "After the customer explicitly confirms the final order summary, create the order in Chatify AI immediately. Returns {created:true, order_number} on success. Returns {created:false, duplicate:true, existing_order_number, existing_items} if an active order with identical items already exists for this conversation — do NOT retry, just answer the customer using the existing order number. Never mention integrations or technical details to the customer.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "product": {"type": "string"}, "size": {"type": "string"}, "color": {"type": "string"}, "quantity": {"type": "integer"}}}},
            "customer_name": {"type": "string"}, "customer_phone": {"type": "string"},
            "address": {"type": "string"}, "city": {"type": "string"},
            "payment_method": {"type": "string", "enum": ["COD", "BANK_TRANSFER"]}
        }, "required": ["items", "customer_name", "customer_phone", "address", "city", "payment_method"]}}},
    {"type": "function", "function": {
        "name": "get_payment_instructions",
        "description": "Provide the merchant's current bank-transfer details and tell the customer to send a clear payment screenshot after transferring. Use when the customer wants to pay online by bank transfer.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "request_human_attention",
        "description": "Flag this conversation for a human agent (refund dispute, angry customer, unknown info, explicit request).",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
]

SHIPPING_FAST = {"lahore", "karachi", "islamabad", "rawalpindi"}


async def _find_product(merchant_id, name):
    return await db.products.find_one({
        "merchant_id": ObjectId(merchant_id),
        "title": {"$regex": re.escape(name), "$options": "i"},
    })


async def dispatch(name: str, args: dict, ctx: dict) -> dict:
    """Execute a tool. ctx = {merchant_id, conversation_id}. Always tenant-scoped."""
    mid = ctx["merchant_id"]
    oid = ObjectId(mid)

    if name == "search_products":
        docs = await db.products.find({
            "merchant_id": oid,
            "title": {"$regex": re.escape(args.get("query", "")), "$options": "i"},
        }).limit(10).to_list(10)
        return {"results": [{"title": d["title"], "price": d["price"], "status": d["status"]} for d in docs]}

    if name == "check_variant_availability":
        p = await _find_product(mid, args["product"])
        if not p:
            return {"found": False, "note": "Product not found in catalog."}
        q = {"merchant_id": oid, "product_id": p["_id"]}
        if args.get("size"):
            q["size"] = {"$regex": f"^{re.escape(args['size'])}$", "$options": "i"}
        if args.get("color"):
            q["color"] = {"$regex": f"^{re.escape(args['color'])}$", "$options": "i"}
        variants = await db.product_variants.find(q).to_list(50)
        if not variants:
            return {"found": False, "product": p["title"], "note": "No matching variant."}
        out = [{"size": v["size"], "color": v["color"], "price": v["price"],
                "inventory": v["inventory"], "available": v["inventory"] > 0} for v in variants]
        return {"found": True, "product": p["title"], "variants": out}

    if name == "find_product_media":
        p = await _find_product(mid, args["product"])
        if not p:
            return {"found": False}
        q = {"merchant_id": oid, "product_id": p["_id"]}
        if args.get("tag"):
            q["tags"] = {"$regex": re.escape(args["tag"].replace(" ", "_")), "$options": "i"}
        m = await db.product_media.find_one(q)
        if not m:
            m = await db.product_media.find_one({"merchant_id": oid, "product_id": p["_id"]})
        if not m:
            return {"found": False, "note": "No media tagged for this product."}
        return {"found": True, "media_type": m["media_type"], "url": m["url"],
                "tags": m.get("tags", []), "filename": m["filename"]}

    if name == "recommend_related":
        if ctx.get("conversation_id"):
            conversation = await db.conversations.find_one({"_id": ObjectId(ctx["conversation_id"]), "merchant_id": oid})
            if conversation and conversation.get("sales_opt_out"):
                return {"results": [], "reason": "customer_declined_recommendations"}
        p = await _find_product(mid, args["product"])
        if not p:
            return {"results": []}
        q = {"merchant_id": oid, "product_id": p["_id"]}
        if args.get("kind"):
            q["kind"] = args["kind"]
        rels = await db.product_relationships.find(q).to_list(20)
        out = []
        for r in rels:
            rp = await db.products.find_one({"_id": r["related_product_id"]})
            if rp:
                out.append({"title": rp["title"], "price": rp["price"], "kind": r["kind"]})
        return {"results": out}

    if name == "validate_address":
        return address_validator.validate(args.get("address", ""), args.get("city"))

    if name == "get_shipping_estimate":
        city = (args.get("city") or "").lower()
        fast = any(c in city for c in SHIPPING_FAST)
        return {"city": args.get("city"), "cost": 200, "currency": "PKR",
                "days": "1-2 working days" if fast else "2-4 working days"}

    if name == "search_knowledge_base":
        docs = await db.knowledge_documents.find({
            "merchant_id": oid,
            "$or": [
                {"content": {"$regex": re.escape(args.get("query", "")), "$options": "i"}},
                {"title": {"$regex": re.escape(args.get("query", "")), "$options": "i"}},
            ],
        }).limit(3).to_list(3)
        if not docs:
            all_docs = await db.knowledge_documents.find({"merchant_id": oid}).limit(3).to_list(3)
            docs = all_docs
        return {"documents": [{"title": d["title"], "category": d["category"], "content": d["content"]} for d in docs]}

    if name == "build_order_summary":
        items = args.get("items", [])
        line_items, subtotal = [], 0
        for it in items:
            p = await _find_product(mid, it.get("product", ""))
            if not p:
                continue
            qv = {"merchant_id": oid, "product_id": p["_id"]}
            if it.get("size"):
                qv["size"] = {"$regex": f"^{re.escape(it['size'])}$", "$options": "i"}
            v = await db.product_variants.find_one(qv)
            price = v["price"] if v else p["price"]
            qty = int(it.get("quantity", 1))
            subtotal += price * qty
            line_items.append({"product": p["title"], "size": it.get("size"),
                               "quantity": qty, "unit_price": price, "line_total": price * qty})
        city = (args.get("city") or "").lower()
        shipping = 200 if line_items else 0
        return {"items": line_items, "subtotal": subtotal, "shipping": shipping,
                "total": subtotal + shipping, "currency": "PKR",
                "payment_method": args.get("payment_method", "COD")}

    if name == "create_shopify_order":
        from services.shopify_orders import create_customer_order

        def _signature(items: list[dict]) -> tuple:
            """Stable signature of a cart so we can detect true duplicates regardless of key order."""
            normalized = []
            for it in items or []:
                normalized.append((
                    (it.get("product") or "").strip().lower(),
                    (it.get("size") or "").strip().lower(),
                    (it.get("color") or "").strip().lower(),
                    int(it.get("quantity", 1) or 1),
                ))
            return tuple(sorted(normalized))

        # Guardrail: only block when the SAME cart (same items+variants+quantities) was already
        # created recently for this conversation. Different items must always be allowed.
        if ctx.get("conversation_id"):
            new_sig = _signature(args.get("items") or [])
            existing = await db.shopify_orders.find({
                "merchant_id": oid,
                "conversation_id": ObjectId(ctx["conversation_id"]),
                "fulfillment_status": {"$nin": ["cancelled", "cancelled_by_customer"]},
            }).sort("created_at", -1).limit(5).to_list(5)
            for order in existing:
                existing_sig = tuple(sorted(
                    (
                        (it.get("title") or "").strip().lower(),
                        (it.get("variant") or "").strip().lower(),
                        "",
                        int(it.get("qty", 1) or 1),
                    )
                    for it in (order.get("items") or [])
                ))
                # Match if the product+variant+qty tuple set matches (color is optional).
                if new_sig and existing_sig and all(
                    any(e[0] == n[0] and (not n[1] or n[1] == e[1]) and n[3] == e[3] for e in existing_sig)
                    for n in new_sig
                ) and len(new_sig) == len(existing_sig):
                    return {
                        "created": False,
                        "duplicate": True,
                        "existing_order_number": order.get("order_number"),
                        "existing_items": order.get("items"),
                        "message": (
                            "The customer already has an order with these exact items. "
                            "Do NOT create another. Tell the customer the existing order number and ask if they want "
                            "an ADDITIONAL separate order (wait for a clear yes) or if they want to change/cancel it."
                        ),
                    }
        result = await create_customer_order(mid, ctx.get("conversation_id"), args)
        # Provider/sync metadata is merchant-internal and must not be exposed to the customer-facing LLM turn.
        return {k: v for k, v in result.items() if k not in {"shopify_synced", "shopify_order_id", "sync_status"}}

    if name == "get_payment_instructions":
        from services.payment_verification import customer_payment_instructions
        return customer_payment_instructions()

    if name == "request_human_attention":
        if ctx.get("conversation_id"):
            await db.conversations.update_one(
                {"_id": ObjectId(ctx["conversation_id"]), "merchant_id": oid},
                {"$set": {"needs_attention": True, "escalation_reason": args.get("reason")}})
        from audit import log_action
        await log_action(mid, "ai_requested_human", actor="ai",
                         conversation_id=ctx.get("conversation_id"), detail={"reason": args.get("reason")})
        return {"flagged": True, "reason": args.get("reason")}

    return {"error": f"unknown tool {name}"}

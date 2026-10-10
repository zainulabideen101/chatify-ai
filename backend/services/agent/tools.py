from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from bson import ObjectId
from database import db
from services.structured_retrieval import StructuredProductQuery, execute_structured_retrieval, MatchStatus

# -----------------
# Models
# -----------------
class SearchResultProduct(BaseModel):
    product_id: str
    title: str
    price_range: str
    matching_variants_count: int
    available_attributes: Dict[str, List[str]]
    variant_prices: Optional[Dict[str, float]] = None
    differing_constraints: List[str] = Field(default_factory=list)
    image: Optional[str] = None
    url: Optional[str] = None

class SearchCatalogResponse(BaseModel):
    status: str
    items: List[SearchResultProduct]
    message: str

# -----------------
# Tools
# -----------------
async def tool_search_catalog(
    merchant_id: str,
    category: Optional[str] = None,
    attributes: Optional[Dict[str, List[str]]] = None,
    negative_attributes: Optional[Dict[str, List[str]]] = None,
    price_max: Optional[float] = None,
    price_min: Optional[float] = None,
    quantity: Optional[int] = 1
) -> SearchCatalogResponse:
    """
    Search the merchant's catalog for products matching the given criteria.
    CRITICAL: 'category' now acts as a UNIVERSAL AI TEXT SEARCH query! 
    - You MUST include the explicit device, brand, product type, and any specific material or feature requested in this SINGLE string (e.g. 'adapter for macbook pro', 'silicone cover for iphone 14 pro max').
    - Do NOT strip the device name! Put device models directly into the 'category' string!
    - Always use singular keywords (e.g. 'case' instead of 'cases').
    """
    candidate_ids = None
    cat_lower = category.lower() if category else ""
    
    # Domain 1: Entity-Completeness Gateway
    # Structurally prevent broad, entity-less catalog dumps that hijack agent logic.
    valid_entity = False
    if category:
        cat_words = set(category.lower().replace('-', ' ').split())
        stop_words = {'for', 'the', 'a', 'in', 'of', 'with', 'color', 'wala', 'discount', 'bulk', 'saste', 'offer'}
        qty_words = {'pack', 'bundle', 'pieces', 'piece', 'pcs'}
        
        # Check if there are any core noun words left after stripping numbers, stopwords, and modifiers
        core_words = {kw for kw in cat_words if not kw.isdigit() and kw not in stop_words and kw not in qty_words}
        if core_words:
            valid_entity = True
            
    if not valid_entity:
        return SearchCatalogResponse(
            status="MISSING_ENTITY",
            items=[],
            message="Error: Category requires a valid core product noun. Empty, purely numeric, or vague searches are not permitted."
        )
        
    # Fetch merchant config for dynamic isolation and synonyms
    mid = ObjectId(merchant_id) if isinstance(merchant_id, str) else merchant_id
    config = await db.merchant_configs.find_one({"merchant_id": str(mid)}) or {}
    isolation_rules = config.get("category_isolation_rules", [])
    category_synonyms = config.get("category_synonyms", {})
    
    # Fetch store intelligence for brand exclusivity
    intel = await db.store_intelligence.find_one({"merchant_id": mid}) or {}
    explicit_exclusions = intel.get("explicit_exclusions", [])
    

    is_broad_precalculated = False
    device_provided_precalculated = False
    if category:
        # Domain 4 (Phase 1): Database-Driven Subtraction Ontology (Broadness Evaluator)
        # Instead of calculating local word frequency (which is biased by the search results), 
        # we deterministically extract store-level structural boundaries from the DB.
        store_stop_words = set()

        # 1. Add DB Category Synonyms (e.g. "case", "cover", "charger")
        for k, syns in category_synonyms.items():
            store_stop_words.add(k.lower())
            for s in syns:
                store_stop_words.update(s.lower().replace('-', ' ').split())

        # 2. Add DB Category Isolation Rules (e.g. "protector", "glass")
        for rule in isolation_rules:
            for kw in rule.get("trigger_keywords", []):
                store_stop_words.update(kw.lower().replace('-', ' ').split())

        # 3. Add purely language/conversational fillers (NO inventory specific terms)
        language_stop_words = {"for", "the", "a", "an", "with", "in", "of", "and", "wala", "wale", "wali", "kya", "tumhare", "paas", "hai", "hain", "mujhe", "chahiye", "dikhao", "bhai", "g", "aur", "mera", "kab", "aayega", "done", "karo", "do", "you", "have", "any", "i", "want", "need", "please", "show", "me", "is", "are", "on", "to"}
        discount_words = {"saste", "sasta", "cheap", "discount", "offer", "bulk"}

        # 4. Add dynamic attribute words (so if user says "iphone cases", "iphone" is removed)
        attr_stop_words = set()
        if attributes:
            for k, vals in attributes.items():
                attr_stop_words.add(k.lower())
                for v in vals:
                    attr_stop_words.update(v.lower().replace('-', ' ').split())

        # 5. Add ecosystems and purely conversational generic device nouns
        ecosystems = [eco.lower() for eco in intel.get("supported_ecosystems", [])]
        for eco in ecosystems:
            attr_stop_words.update(eco.split())
        attr_stop_words.update({"phone", "mobile", "tablet", "device", "laptop", "smartwatch"})

        dynamic_stop_words = store_stop_words | language_stop_words | discount_words | attr_stop_words

        query_words = set(category.lower().replace('-', ' ').split())
        meaningful_words = {w for w in query_words if w not in dynamic_stop_words and w.rstrip('s') not in dynamic_stop_words}

        is_broad_precalculated = len(meaningful_words) == 0
        device_provided_precalculated = bool(attributes and "device" in attributes)



    if category:
        from services.semantic_search import semantic_search
        sem_results = await semantic_search(str(merchant_id), category, limit=250, threshold=0.55 if not is_broad_precalculated else 0.1)
        candidate_ids = [str(r.get("_id")) for r in sem_results if r.get("_id")]
        
        if not candidate_ids:
            return SearchCatalogResponse(
                status="NO_MATCH",
                items=[],
                message="No exact matches found for these requirements."
            )

    query = StructuredProductQuery(
        merchant_id=merchant_id,
        category=category,
        attribute_constraints=attributes or {},
        negative_attribute_constraints=negative_attributes or {},
        price_max=price_max,
        price_min=price_min,
        availability_required=True,
        semantic_candidate_ids=candidate_ids
    )
    
    res = await execute_structured_retrieval(query)
    
    if res.status == MatchStatus.NO_MATCH:
        return SearchCatalogResponse(
            status="NO_MATCH",
            items=[],
            message="No exact matches found for these requirements."
        )
        
    items = []
    for g in res.groups:
        if not g.matching_variants: continue
        
        prices = [v.price for v in g.matching_variants]
        min_price, max_price = min(prices), max(prices)
        price_range = f"Rs {min_price}" if min_price == max_price else f"Rs {min_price} - Rs {max_price}"
        
        avail_attrs = {}
        diff_constraints = set()
        for v in g.matching_variants:
            for k, val in v.attributes.items():
                if k not in avail_attrs: avail_attrs[k] = set()
                if isinstance(val, list):
                    avail_attrs[k].update([str(x) for x in val])
                else:
                    avail_attrs[k].add(str(val))
            diff_constraints.update(v.differing_constraints)
            
        variant_prices = {}
        if min_price != max_price:
            for v in g.matching_variants:
                if len(variant_prices) >= 15:
                    variant_prices["[TOO_MANY_VARIANTS_TO_LIST_ALL_PRICES]"] = -1.0
                    break
                
                attr_strs = []
                for val in v.attributes.values():
                    val_str = str(val[0]) if isinstance(val, list) else str(val)
                    attr_strs.append(f"{val_str}")
                
                v_name = " / ".join(attr_strs) if attr_strs else "Default"
                if v_name not in variant_prices:
                    variant_prices[v_name] = v.price
            
        # Domain 2: Categorical Boundaries via Metadata Masking
        # Instead of hardcoded keyword exclusions, we enforce a strict semantic threshold.
        # If the user specified a category, we drop items whose product_type differs drastically,
        # unless they explicitly share attributes.
        items.append(
            SearchResultProduct(
                product_id=g.product_id,
                title=g.product_title,
                price_range=price_range,
                matching_variants_count=len(g.matching_variants),
                available_attributes={k: list(v) for k, v in avail_attrs.items()},
                variant_prices=variant_prices if variant_prices else None,
                differing_constraints=list(diff_constraints),
                image=getattr(g.matching_variants[0], 'image', None),
                url=getattr(g.matching_variants[0], 'url', None)
            )
        )
    # Domain 1.5: Category Isolation (Strict Hard Filters via Dynamic Merchant Config)
    if category and items:
        
        # Apply dynamic isolation rules
        for rule in isolation_rules:
            triggers = rule.get("trigger_keywords", [])
            includes = rule.get("must_include_keywords", [])
            excludes = rule.get("must_exclude_keywords", [])
            
            if any(t in cat_lower for t in triggers):
                if includes:
                    items = [itm for itm in items if any(inc in itm.title.lower() for inc in includes)]
                if excludes:
                    items = [itm for itm in items if not any(exc in itm.title.lower() for exc in excludes)]
                break # Only apply the first matching rule
        
        # Apply store intelligence explicit exclusions (Brand Exclusivity)
        if explicit_exclusions and items:
            items = [itm for itm in items if not any(exc.lower() in itm.title.lower() for exc in explicit_exclusions)]

    # Domain 2: Categorical Boundaries via Metadata Masking / Anti-Drift Semantic Verifier
    drift_msg = ""
    if category and items and category.lower() not in ["discount", "bulk", "saste", "offer"]:
        cat_words = set(category.lower().replace('-', ' ').split())
        stop_words = {'for', 'the', 'a', 'in', 'of', 'with', 'color', 'wala'}
        
        # Add dynamic brand names to stop words (e.g. apple, iphone) based on supported ecosystems
        ecosystems = [eco.lower() for eco in intel.get("supported_ecosystems", [])]
        for eco in ecosystems:
            stop_words.update(eco.split())
            
        qty_words = {'pack', 'bundle', 'pieces', 'piece', 'pcs'}
        raw_key_words = {kw for kw in (cat_words - stop_words) if not kw.isdigit() and kw not in qty_words}
        
        enforcement_words = raw_key_words
        
        if enforcement_words:
            matched_items = []
            partial_match_items = []
            for itm in items:
                # Truncate title at common accessory delimiters to prevent lexical bleed (e.g., "+ Free Cover")
                raw_title = itm.title.lower()
                for delim in [" with ", " + ", " free ", " including ", " inc "]:
                    if delim in raw_title:
                        raw_title = raw_title.split(delim)[0]
                
                title_words = set(raw_title.replace('-', ' ').split())
                
                # Dynamic Synonym expansion to prevent false drift
                expanded_title_words = set(title_words)
                for syn_key, syn_list in category_synonyms.items():
                    # Flatten the synonym list into individual words
                    flat_syn_words = set(syn_key.split())
                    for syn in syn_list:
                        flat_syn_words.update(syn.split())
                        
                    # Check if the title contains ALL words of the syn_key OR ALL words of any syn in syn_list
                    match_syn = False
                    key_words = syn_key.split()
                    if all(w in title_words or w + 's' in title_words or w.rstrip('s') in title_words for w in key_words):
                        match_syn = True
                    else:
                        for syn in syn_list:
                            syn_words = syn.split()
                            if all(w in title_words or w + 's' in title_words or w.rstrip('s') in title_words for w in syn_words):
                                match_syn = True
                                break
                                
                    if match_syn:
                        expanded_title_words.update(flat_syn_words)
                        
                # Inject attribute values (like 'Transparent' or '14 Pro Max') into the lexical boundary
                for attr_key, attr_vals in itm.available_attributes.items():
                    for val in attr_vals:
                        expanded_title_words.update(val.lower().replace('-', ' ').split())
                
                # To prevent drift, check if ALL enforcement words exist in the expanded title (accounting for plurals)
                match = True
                for kw in enforcement_words:
                    if kw not in expanded_title_words and kw.rstrip('s') not in expanded_title_words and kw + 's' not in expanded_title_words:
                        match = False
                        break
                
                if match:
                    matched_items.append(itm)
                
                # Also track partial matches for fallback
                matched_kws = [kw for kw in enforcement_words if kw in expanded_title_words or kw.rstrip('s') in expanded_title_words or kw + 's' in expanded_title_words]
                if matched_kws:
                    missing_kws = [kw for kw in enforcement_words if kw not in matched_kws]
                    partial_match_items.append((itm, matched_kws, missing_kws))
            
            if matched_items:
                items = matched_items
            else:
                # Universal Agentic Fallback
                # Pass up to 15 partial matches to the LLM, allowing the LLM's world knowledge 
                # to act as the semantic filter rather than relying on brittle lexical boundaries.
                items = [tup[0] for tup in partial_match_items[:15]]
            
            if not items:
                # If there are not even partial keyword matches, don't hallucinate random items
                return SearchCatalogResponse(
                    status="NO_MATCH",
                    items=[],
                    message="No products found matching these requirements."
                )

    # Domain 5: Virtual Catalog Consolidation Engine
    # If multiple single items share the same base title (e.g., "Dark Blue Silicone Cover", "Black Silicone Cover"),
    # we merge them into a single virtual product to prevent LLM truncation and sprawling output.
    consolidated_items = []
    processed_indices = set()
    
    for i, itm1 in enumerate(items):
        if i in processed_indices:
            continue
            
        # Domain 5 Exclusion: Never consolidate Bundles (otherwise Domain 3 math fails)
        if "pack" in itm1.title.lower() or "bundle" in itm1.title.lower() or "in 1" in itm1.title.lower():
            consolidated_items.append(itm1)
            processed_indices.add(i)
            continue
            
        cluster = [itm1]
        w1 = itm1.title.lower().split()
        
        for j in range(i + 1, len(items)):
            if j in processed_indices:
                continue
            itm2 = items[j]
            
            # Domain 5 Exclusion for inner loop
            if "pack" in itm2.title.lower() or "bundle" in itm2.title.lower() or "in 1" in itm2.title.lower():
                continue
                
            w2 = itm2.title.lower().split()
            
            common = []
            for k in range(1, min(len(w1), len(w2)) + 1):
                if w1[-k] == w2[-k]:
                    common.insert(0, w1[-k])
                else:
                    break
                    
            if len(common) >= 2:
                cluster.append(itm2)
                processed_indices.add(j)
                
        if len(cluster) > 1:
            common_suffix = cluster[0].title.split()
            for c_itm in cluster[1:]:
                c_words = c_itm.title.split()
                temp_common = []
                for k in range(1, min(len(common_suffix), len(c_words)) + 1):
                    if common_suffix[-k].lower() == c_words[-k].lower():
                        temp_common.insert(0, common_suffix[-k])
                    else:
                        break
                common_suffix = temp_common
                
            base_title = " ".join(common_suffix).title() if common_suffix else cluster[0].title
            
            variants = []
            prices = []
            avail_attrs = {}
            for c_itm in cluster:
                c_words = c_itm.title.split()
                prefix_len = len(c_words) - len(common_suffix)
                prefix = " ".join(c_words[:prefix_len])
                variant_name = prefix.strip() if prefix.strip() else "Standard"
                variants.append(variant_name)
                
                try:
                    p_str = c_itm.price_range.split('-')[0].replace('Rs', '').strip()
                    prices.append(float(p_str))
                except:
                    pass
                
                for k, v in c_itm.available_attributes.items():
                    if k not in avail_attrs:
                        avail_attrs[k] = []
                    avail_attrs[k].extend(v)
            
            avail_attrs["Available Options"] = list(set([v for v in variants if v]))
            for k in avail_attrs:
                avail_attrs[k] = list(set(avail_attrs[k]))
            
            # Domain 5.5: Multi-Variant Visual Preservation Engine
            # Prevents destructive image/URL loss during consolidation by explicitly storing variants' media assets
            visual_catalog = []
            for c_itm in cluster:
                if c_itm.url and c_itm.image:
                    visual_catalog.append(f"{c_itm.title} (URL: {c_itm.url}, Image: {c_itm.image})")
            if visual_catalog:
                avail_attrs["Visual Catalog (Render product cards for these if <= 4 options, else just list names)"] = list(set(visual_catalog))
                
            min_p, max_p = (min(prices), max(prices)) if prices else (0, 0)
            price_range = f"Rs {min_p:.1f}" if min_p == max_p else f"Rs {min_p:.1f} - Rs {max_p:.1f}"
            
            merged_item = SearchResultProduct(
                product_id=cluster[0].product_id,
                title=base_title + " (Multiple Options)",
                price_range=price_range,
                matching_variants_count=sum(c.matching_variants_count for c in cluster),
                available_attributes=avail_attrs,
                differing_constraints=cluster[0].differing_constraints,
                image=cluster[0].image,
                url=cluster[0].url
            )
            consolidated_items.append(merged_item)
        else:
            consolidated_items.append(itm1)
            
    items = consolidated_items
    
    # Domain 5.1: High-Density Attribute Truncation Engine
    # Prevents LLM context overflow/vomiting when a single product has massive variant dimensions
    for itm in items:
        for k, v in list(itm.available_attributes.items()):
            if isinstance(v, list) and not k.startswith("Visual Catalog"):
                threshold = 5 if 'comp' in k.lower() or 'device' in k.lower() else 10
                if len(v) > threshold:
                    itm.available_attributes[k] = [f"Many options available ({len(v)} total). You MUST ask the user to specify their {k}."]

    # Domain 4: Semantic Aggregation & Variant Unrolling Layer
    # We group products by their inferred sub-category/type to prevent messy flat lists.
    grouped_items = {}
    for itm in items:
        # Extract a generic type from the title
        type_hint = itm.title.split()[-1] if itm.title else "Other"
        for cat, syns in category_synonyms.items():
            if any(syn in itm.title.lower() for syn in syns):
                type_hint = cat.title()
                break
            
        if type_hint not in grouped_items:
            grouped_items[type_hint] = []
        grouped_items[type_hint].append(itm.title)

    # Domain 6: Topological Entropy Gateway
    # Mathematically detect broad/vague queries based on result fragmentation
    bundle_items = [itm.title for itm in items if "pack" in itm.title.lower() or "bundle" in itm.title.lower() or "in 1" in itm.title.lower()]
    bundle_hint = ""
    if bundle_items:
        # Dynamically extract the categories of the bundles to prevent hallucinating bundles for unsupported materials
        bundle_styles = set()
        for b_title in bundle_items:
            # Extract basic descriptor, e.g., "Pack of 2 Silicone Covers" -> "Silicone Covers"
            parts = b_title.lower().split("pack of ")
            if len(parts) > 1:
                style = parts[1].split()[1:] # drop the number
                bundle_styles.add(" ".join(style).title())
            else:
                bundle_styles.add(b_title.split()[-2].title() + " " + b_title.split()[-1].title()) # Rough fallback
        
        bundle_hint = f"\n\nSYSTEM DIRECTIVE: We DO have cost-saving bulk bundles/packs available! However, they are ONLY available for specific styles: {', '.join(bundle_styles)}. You MUST briefly mention this limitation when offering bundles."
        
        if quantity and quantity > 1:
            bundle_hint += "\n\nBUNDLE PRESENTATION DIRECTIVE: The user wants multiple pieces. When presenting a Bundle or Pack, DO NOT list out random individual variants! You MUST simply present the Bundle as a single product, and inform the user that they can choose their own preferred options/variants for the items inside the bundle."

    
    if category:
        bundle_hint += f"\n\nSEMANTIC BRIDGE DIRECTIVE: The user requested '{category}'. If their exact requested style, texture, or material is not explicitly named in the available product titles, you MUST use your world knowledge to determine which of the available options is materially or functionally equivalent, and proactively recommend it as the closest match!"
        
        if any(w in cat_lower for w in ["discount", "bulk", "saste", "offer", "cheap"]):
            bundle_hint += "\n\nDISCOUNT DIRECTIVE: DO NOT say you couldn't find 'cheap' or 'saste' items! Confidently present the available categories, and strongly emphasize that they can get them at a discount if they buy bundles."
            
        is_broad = is_broad_precalculated
        device_provided = device_provided_precalculated
        
        # Rule: If customer provided BOTH a specific style (not broad) AND a device, BYPASS the category list.
        if not is_broad and device_provided:
            return SearchCatalogResponse(
                status="SUCCESS",
                items=items[:15],
                message=(
                    "Success: Found highly specific matching products for the requested device and category.\n"
                    "[CRITICAL AGENT INSTRUCTION]\n"
                    "You MUST present the individual products, prices, and images to the user! Do NOT output a list of categories."
                ) + bundle_hint
            )
            
        # Domain 6 handles broadness naturally via len(grouped_items) > 3
        sample_cats = list(grouped_items.keys())
        if len(grouped_items) > 3 or is_broad:
            # Summarize the distinct styles inside the groups to give them a better list of categories
            distinct_styles = set()
            for k, v in grouped_items.items():
                for t in v:
                    distinct_styles.add(t.title())
            if distinct_styles:
                sample_cats = list(distinct_styles)[:8]
            
            sample_cats = [f"{cat} Collection" for cat in sample_cats]
            
            return SearchCatalogResponse(
                status="NEEDS_CLARIFICATION",
                items=[],
                message=(
                    f"Success: We found multiple categories (e.g., {', '.join(sample_cats)}). "
                    "[CRITICAL AGENT INSTRUCTION]\n"
                    f"The user's query was very broad. You MUST NOT present any individual products, prices, images, or links! You MUST ONLY output a simple text list of the available categories ({', '.join(sample_cats)}) and ask them to choose."
                ) + bundle_hint
            )
        else:
            return SearchCatalogResponse(
                status="SUCCESS",
                items=items[:15],
                    message=(
                        f"Success: We found specific matching items, but they are distributed across multiple collections ({', '.join(sample_cats)}). "
                        "[CRITICAL AGENT INSTRUCTION]\n"
                        "You MUST present the individual products and images that perfectly match their specific style/color (e.g. mapping 'transparent' to 'Clear Case'), and ignore the mismatched ones."
                    ) + bundle_hint
                )

    # Domain 4 Extractor (Phase 2): Adjective Extraction Engine & Categorical Summaries
    # Mathematically isolate the distinguishing styles/materials from long-tail titles
    categorical_summaries = []
    if grouped_items:
        for k, v in grouped_items.items():
            if len(v) > 1:
                # Find common baseline words across all titles in this group
                split_titles = [t.split() for t in v]
                common_baseline = set(split_titles[0])
                for t in split_titles[1:]:
                    common_baseline.intersection_update(t)
                
                # Extract adjectives
                styles = set()
                for t in split_titles:
                    diff = [w for w in t if w not in common_baseline]
                    if diff:
                        styles.add(" ".join(diff))
                
                if styles:
                    # Domain 6.5: Vertical Entropy Limit (Deep Fragmentation Gateway)
                    if len(styles) > 5 and category:
                        sample_models = list(styles)
                        sample_collections = [f"{model} Collection" for model in sample_models[:5]]
                        
                        if is_broad:
                            return SearchCatalogResponse(
                                status="NEEDS_CLARIFICATION",
                                items=[],
                                message=(
                                    f"Success: We have many {k.lower()} options in stock (e.g., {', '.join(sample_collections)}). "
                                    "[CRITICAL AGENT INSTRUCTION]\n"
                                    f"The user's query was very broad. You MUST NOT present any individual products, prices, images, or links! You MUST ONLY output a simple text list of the available categories/styles ({', '.join(sample_collections)}) and ask them to choose."
                                ) + bundle_hint
                            )
                        else:
                            return SearchCatalogResponse(
                                status="SUCCESS",
                                items=items[:15],
                                message=(
                                    f"Success: We have specific matching items in stock (e.g., {', '.join(sample_collections)}). "
                                    "[CRITICAL AGENT INSTRUCTION]\n"
                                    "You MUST present the individual products and images that perfectly match their specific style, and ignore the mismatched ones."
                                ) + bundle_hint
                            )
                        
                    styles_with_collection = [f"{s} Collection" for s in sorted(list(styles))]
                    categorical_summaries.append(f"{k} options available: {', '.join(styles_with_collection)}")
                else:
                    categorical_summaries.append(f"{k} Collection: {len(v)} variants available")
            else:
                categorical_summaries.append(f"{k} Collection: {v[0]}")
                
    aggregation_msg = "\n[CATEGORICAL SUMMARY]\n" + "\n".join(categorical_summaries) if categorical_summaries else ""

    message = f"Found {len(items)} matching products." + aggregation_msg
    if drift_msg:
        message += drift_msg
    
    # Ensure Semantic Bridge and Bundle Hint are preserved if we didn't trigger Domain 6 clarification
    if bundle_hint:
        message += bundle_hint
    global_directive = (
        "\n\nCRITICAL PRESENTATION DIRECTIVE: When you need the user to clarify, follow this strict rule based on the context:\n"
        "1. If the user asked a BROAD query (like 'phone case', 'cover', 'iPhone 14 case') where multiple different CATEGORIES (e.g. Silicone, Polo, Clear, Matt Magsafe) are available, DO NOT present individual products, prices, images, or links! Instead, ONLY list the text names of the available categories and ask them which type they prefer.\n"
        "2. If the user asked for a SPECIFIC category or style (like 'transparent cover', 'screen protector', '20W charger') but you just need to know their exact device model to fetch the right variant, you MUST present the matching product(s) with their name, image, and price range FIRST, and then ask for the model. NEVER hide a specifically requested product while asking for the device model!"
    )
    message += global_directive
    # Domain 3: Dynamic Combinatorics & Resolution Engine (Multi-Tenant Bundle Math)
    # Proactively check for bundles in the returned semantic context
    bundle_items = [itm for itm in items if ("pack" in itm.title.lower() or "bundle" in itm.title.lower() or "in 1" in itm.title.lower())]
    single_items = [itm for itm in items if itm not in bundle_items]
    
    if bundle_items and single_items:
        single_prices = []
        for itm in single_items:
            try:
                p_str = itm.price_range.split('-')[0].replace('Rs', '').strip()
                single_prices.append(float(p_str))
            except: pass
            
        if single_prices:
            avg_single = sum(single_prices) / len(single_prices)
            bundle_msg = "\n[DYNAMIC BUNDLE RESOLUTION ENGINE]\n"
            
            import re
            
            # dynamically parse bundle quantities
            parsed_bundles = []
            for b in bundle_items:
                m = re.search(r'(?:pack of|bundle of|in 1)\s*(\d+)|(\d+)\s*(?:pack|in 1|-pack)', b.title.lower())
                qty = int(m.group(1) or m.group(2)) if m else 0
                if qty > 1:
                    try:
                        p = float(b.price_range.split('-')[0].replace('Rs', '').strip())
                        parsed_bundles.append({"qty": qty, "price": p, "title": b.title})
                    except: pass
            
            parsed_bundles.sort(key=lambda x: x["qty"], reverse=True)
            
            if parsed_bundles:
                # Domain 3.5: Bundle Entropy Gateway
                # Mathematically detect vague queries (e.g. missing device model) by checking if multiple distinct bundles share the same quantity (e.g. two different 3-packs).
                quantities = [b['qty'] for b in parsed_bundles]
                has_duplicate_qty = len(set(quantities)) < len(quantities)
                
                if has_duplicate_qty and not (quantity and quantity == 1):
                    return SearchCatalogResponse(
                        status="NEEDS_CLARIFICATION",
                        items=[],
                        message="Success: We have many cost-saving bundle options available! However, the query is spanning multiple distinct product lines. You MUST explicitly ask the user to specify their exact device model and preferred style so we can calculate their specific bundle discount."
                    )
                
                is_bundle_seeking = False
                if category:
                    if "discount" in cat_lower or "bulk" in cat_lower or "pack" in cat_lower or "bundle" in cat_lower:
                        is_bundle_seeking = True
                
                show_bundles = False
                if (quantity and quantity > 1) or is_bundle_seeking:
                    show_bundles = True
                
                if show_bundles:
                    bundle_msg += "SYSTEM DIRECTIVE: Bundles are available! You MUST proactively offer these bundle options to the user, explaining that they are more cost-effective:\n"
                    for b in parsed_bundles:
                        bundle_savings = (b['qty'] * avg_single) - b['price']
                        bundle_msg += f"- {b['title']} for Rs {b['price']:.0f} (Saves you Rs {bundle_savings:.0f} compared to buying {b['qty']} singles at Rs {avg_single:.0f} each)\n"
                    
                    if quantity and quantity > 1:
                        # Basic knapsack/greedy resolution
                        combo_str = ""
                        total_cost = 0
                        rem_qty = quantity
                        for b in parsed_bundles:
                            if rem_qty >= b["qty"]:
                                count = rem_qty // b["qty"]
                                total_cost += count * b["price"]
                                combo_str += f"{count}x {b['title']} "
                                rem_qty %= b["qty"]
                        
                        if rem_qty == 0:
                            savings = (quantity * avg_single) - total_cost
                            bundle_msg += f"\nMATH RESOLUTION: User requested {quantity} items. You MUST offer this exact combination: {combo_str.strip()} for Rs {total_cost:.0f}. State that this saves them Rs {savings:.0f} compared to buying singles!"
                        else:
                            bundle_msg += f"\nMATH RESOLUTION: User requested {quantity} items. We couldn't form an exact combo, but explicitly offer the bundles listed above."
                    else:
                        bundle_msg += f"\nMATH RESOLUTION: User wants a discount. You MUST offer the bundles listed above and explicitly state the math: buying a bundle saves them money compared to buying singles at Rs {avg_single:.0f} each!"
                    
                    message += bundle_msg

    if res.status == MatchStatus.VERIFIED_ALTERNATIVE:
        return SearchCatalogResponse(
            status="VERIFIED_ALTERNATIVE",
            items=items,
            message="[SYSTEM_FLAG: EXACT ATTRIBUTES NOT FOUND. SEMANTIC FALLBACK ENGAGED]\nWARNING: You MUST explicitly tell the customer that we do NOT have their exact request. However, if their requested style/material (e.g. 'leather') is not found, you MUST use your world knowledge to dynamically map it to the closest available option in the returned items (e.g. mapping 'leather' to 'Polo') and proactively recommend it!\n" + message
        )
        
    return SearchCatalogResponse(
        status="SUCCESS",
        items=items,
        message=message
    )

async def tool_get_product(
    merchant_id: str,
    product_id: str
) -> Dict[str, Any]:
    """
    Get detailed information about a specific product by its ID.
    """
    try:
        pid = ObjectId(product_id)
    except:
        return {"status": "ERROR", "message": "Invalid product_id format. product_id must be a 24-character hex string. You MUST use search_catalog to search by title/category to find the correct product_id first."}
        
    p = await db.products.find_one({"_id": pid, "merchant_id": ObjectId(merchant_id)})
    if not p:
        return {"status": "NOT_FOUND", "message": "Product not found."}
        
    variants = []
    async for v in db.product_variants.find({"product_id": {"$in": [pid, str(pid)]}}):
        variants.append({
            "variant_id": str(v["_id"]),
            "price": v.get("price"),
            "attributes": v.get("attributes", {}),
            "inventory": v.get("inventory", 0)
        })
        
    return {
        "status": "SUCCESS",
        "product_id": str(p["_id"]),
        "title": p.get("title"),
        "product_type": p.get("product_type"),
        "description": p.get("body_html", ""),
        "image": p.get("image"),
        "url": p.get("online_store_url"),
        "variants": variants
    }

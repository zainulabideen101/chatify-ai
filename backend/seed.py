"""Startup seeding: owner account, indexes, and representative demo data for the
owner merchant so the dashboard shell is meaningful. Idempotent.

NOTE: seeded conversations/orders are illustrative tenant data for the dashboard
shell — they are NOT faked Shopify/WhatsApp API responses. Integration status
genuinely reflects "not connected" until real credentials are added.
"""
import os
from datetime import datetime, timezone, timedelta
from bson import ObjectId

from database import db
from security import hash_password, verify_password
from models import DefaultAISettings


def _iso(dt): return dt.isoformat()


async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.login_attempts.create_index("identifier")
    await db["daily_chat_usage"].create_index(
        [("merchant_id", 1), ("day", 1), ("customer_phone", 1)], unique=True)
    await db["billing_subscriptions"].create_index("merchant_id", unique=True)
    await db.merchants.create_index("store_name")
    for coll in ["products", "product_variants", "product_media", "customers",
                 "conversations", "messages", "shopify_orders", "abandoned_orders",
                 "recommendation_events", "audit_logs", "business_rules",
                 "knowledge_documents", "whatsapp_templates", "billing_subscriptions",
                 "daily_chat_usage"]:
        await db[coll].create_index("merchant_id")


async def seed_owner_and_demo():
    email = os.environ.get("ADMIN_EMAIL", "").lower().strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email:
        return

    now = datetime.now(timezone.utc)
    existing = await db.users.find_one({"email": email})
    if existing:
        # keep password in sync with .env
        if password and not verify_password(password, existing["password_hash"]):
            await db.users.update_one({"_id": existing["_id"]},
                                      {"$set": {"password_hash": hash_password(password)}})
        return  # demo data already seeded on first run

    merchant_id = (await db.merchants.insert_one({
        "store_name": "TechGear Threads",
        "currency": "PKR", "country": "PK", "timezone": "Asia/Karachi",
        "contact_phone": "+92 300 1234567", "created_at": _iso(now),
    })).inserted_id

    await db.users.insert_one({
        "email": email, "password_hash": hash_password(password or "changeme123"),
        "name": "Store Owner", "role": "owner", "merchant_id": merchant_id, "created_at": _iso(now),
    })
    await db.ai_settings.insert_one({"merchant_id": merchant_id, **DefaultAISettings.make()})

    # ---- Products + variants + media ----
    products = [
        {"title": "Black Hoodie", "price": 2999, "sold_count": 142, "status": "active",
         "img": "https://images.unsplash.com/photo-1556821840-3a63f95609a7?w=400",
         "sizes": ["Small", "Medium", "Large", "XL"], "colors": ["Black"]},
        {"title": "Matching Joggers", "price": 2499, "sold_count": 88, "status": "active",
         "img": "https://images.unsplash.com/photo-1552902865-b72c031ac5ea?w=400",
         "sizes": ["Medium", "Large", "XL"], "colors": ["Black", "Grey"]},
        {"title": "Premium Zip Hoodie", "price": 3499, "sold_count": 61, "status": "active",
         "img": "https://images.unsplash.com/photo-1620799140408-edc6dcb6d633?w=400",
         "sizes": ["Medium", "Large"], "colors": ["Navy", "Charcoal"]},
        {"title": "Classic Cap", "price": 999, "sold_count": 120, "status": "active",
         "img": "https://images.unsplash.com/photo-1588850561407-ed78c282e89b?w=400",
         "sizes": ["One Size"], "colors": ["Black", "White"]},
    ]
    product_ids = {}
    for p in products:
        pid = (await db.products.insert_one({
            "merchant_id": merchant_id, "title": p["title"], "price": p["price"],
            "status": p["status"], "sold_count": p["sold_count"], "image": p["img"],
            "shopify_product_id": None, "created_at": _iso(now),
        })).inserted_id
        product_ids[p["title"]] = pid
        for color in p["colors"]:
            for size in p["sizes"]:
                await db.product_variants.insert_one({
                    "merchant_id": merchant_id, "product_id": pid, "color": color,
                    "size": size, "price": p["price"],
                    "inventory": 12 if size != "Medium" else (0 if p["title"] == "Black Hoodie" and color == "Black" else 8),
                    "sku": f"{p['title'][:3].upper()}-{color[:2].upper()}-{size[:1]}",
                    "shopify_variant_id": None,
                })
        await db.product_media.insert_one({
            "merchant_id": merchant_id, "product_id": pid, "variant_id": None,
            "filename": f"{p['title'].lower().replace(' ', '-')}-real.jpg",
            "media_type": "image", "url": p["img"], "color": p["colors"][0], "size": None,
            "tags": ["real_photo"], "description": f"Real photo of {p['title']}", "created_at": _iso(now),
        })

    # relationships (cross-sell)
    await db.product_relationships.insert_one({
        "merchant_id": merchant_id, "product_id": product_ids["Black Hoodie"],
        "related_product_id": product_ids["Matching Joggers"], "kind": "cross_sell", "created_at": _iso(now),
    })
    await db.product_relationships.insert_one({
        "merchant_id": merchant_id, "product_id": product_ids["Black Hoodie"],
        "related_product_id": product_ids["Premium Zip Hoodie"], "kind": "upsell", "created_at": _iso(now),
    })

    # ---- Customers ----
    customers = [
        {"name": "Ali Raza", "phone": "+92 321 1111111", "lang": "Roman Urdu",
         "orders": 7, "delivered": 6, "cancelled": 1, "size": "Large", "city": "Lahore"},
        {"name": "Sana Khan", "phone": "+92 333 2222222", "lang": "English",
         "orders": 2, "delivered": 2, "cancelled": 0, "size": "Medium", "city": "Karachi"},
        {"name": "Bilal Ahmed", "phone": "+92 300 3333333", "lang": "Urdu",
         "orders": 0, "delivered": 0, "cancelled": 0, "size": None, "city": "Islamabad"},
    ]
    cust_ids = {}
    for c in customers:
        cid = (await db.customers.insert_one({
            "merchant_id": merchant_id, "name": c["name"], "phone": c["phone"],
            "preferred_language": c["lang"], "total_orders": c["orders"],
            "delivered_orders": c["delivered"], "cancelled_orders": c["cancelled"],
            "refused_orders": 0, "failed_deliveries": 0, "city": c["city"],
            "created_at": _iso(now),
        })).inserted_id
        cust_ids[c["name"]] = cid
        await db.customer_memory.insert_one({
            "merchant_id": merchant_id, "customer_id": cid,
            "preferred_size": c["size"], "preferred_language": c["lang"],
            "last_purchase": "Black Hoodie" if c["orders"] else None,
            "interests": ["hoodies"] if c["orders"] else [],
        })

    # ---- Orders ----
    orders = [
        {"cust": "Ali Raza", "num": 1042, "total": 3199, "attr": "AI", "pay": "COD",
         "ff": "delivered", "items": [{"title": "Black Hoodie", "variant": "Large", "qty": 1, "price": 2999}]},
        {"cust": "Sana Khan", "num": 1043, "total": 5498, "attr": "AI_ASSISTED", "pay": "COD",
         "ff": "delivered", "items": [{"title": "Matching Joggers", "variant": "Medium", "qty": 1, "price": 2499},
                                       {"title": "Black Hoodie", "variant": "Medium", "qty": 1, "price": 2999}]},
        {"cust": "Ali Raza", "num": 1044, "total": 999, "attr": "HUMAN", "pay": "COD",
         "ff": "cancelled", "items": [{"title": "Classic Cap", "variant": "One Size", "qty": 1, "price": 999}]},
    ]
    for i, o in enumerate(orders):
        await db.shopify_orders.insert_one({
            "merchant_id": merchant_id, "customer_id": cust_ids[o["cust"]],
            "order_number": o["num"], "shopify_order_id": None, "total": o["total"],
            "attribution": o["attr"], "payment_method": o["pay"], "fulfillment_status": o["ff"],
            "items": o["items"], "conversation_id": None,
            "created_at": _iso(now - timedelta(days=i + 1)),
        })

    # ---- Conversations + messages ----
    convos = [
        {"cust": "Ali Raza", "mode": "AI_ACTIVE", "attention": False, "resolved": True,
         "low_conf": False, "takeover": False, "value": 3199,
         "last": "Order confirm kar doon?",
         "msgs": [("inbound", "customer", "bhai black hoodie medium mein hai?"),
                  ("outbound", "ai", "Jee, Black Hoodie Medium mein available hai. Rs. 2,999 ki hai."),
                  ("inbound", "customer", "real pic bhejo"),
                  ("outbound", "ai", "[image: black-hoodie-real.jpg]"),
                  ("inbound", "customer", "acha order kar do")]},
        {"cust": "Sana Khan", "mode": "HUMAN_ACTIVE", "attention": True, "resolved": False,
         "low_conf": True, "takeover": True, "value": 5498,
         "last": "Can I get a refund on my last order?",
         "msgs": [("inbound", "customer", "Can I get a refund on my last order?"),
                  ("outbound", "ai", "Let me check that for you.")]},
        {"cust": "Bilal Ahmed", "mode": "AI_ACTIVE", "attention": False, "resolved": False,
         "low_conf": False, "takeover": False, "value": 0,
         "last": "kitne ki hai?",
         "msgs": [("inbound", "customer", "premium hoodie dikhao"),
                  ("outbound", "ai", "Premium Zip Hoodie available hai — Navy aur Charcoal mein. Rs. 3,499."),
                  ("inbound", "customer", "kitne ki hai?")]},
    ]
    for c in convos:
        cid = (await db.conversations.insert_one({
            "merchant_id": merchant_id, "customer_id": cust_ids[c["cust"]],
            "customer_name": c["cust"], "customer_phone": None,
            "mode": c["mode"], "needs_attention": c["attention"], "ai_resolved": c["resolved"],
            "low_confidence": c["low_conf"], "had_human_takeover": c["takeover"],
            "conversation_value": c["value"], "intent": "ORDER_REQUEST",
            "last_message": c["last"], "last_message_at": _iso(now),
            "ai_summary": {"customer": c["cust"], "wants": "Black Hoodie", "recommended_action": "Check order status"} if c["takeover"] else None,
            "created_at": _iso(now),
        })).inserted_id
        for j, (direction, sender, body) in enumerate(c["msgs"]):
            await db.messages.insert_one({
                "conversation_id": cid, "merchant_id": merchant_id, "direction": direction,
                "sender": sender, "body": body, "status": "delivered",
                "created_at": _iso(now - timedelta(minutes=len(c["msgs"]) - j)),
            })

    # ---- Abandoned orders ----
    await db.abandoned_orders.insert_one({
        "merchant_id": merchant_id, "customer_id": cust_ids["Bilal Ahmed"],
        "customer_name": "Bilal Ahmed", "product": "Premium Zip Hoodie", "variant": "Large",
        "quantity": 1, "order_value": 3499, "status": "abandoned",
        "reason": "stopped_responding", "last_interaction": _iso(now - timedelta(hours=3)),
    })
    await db.abandoned_orders.insert_one({
        "merchant_id": merchant_id, "customer_id": cust_ids["Ali Raza"],
        "customer_name": "Ali Raza", "product": "Classic Cap", "variant": "One Size",
        "quantity": 2, "order_value": 1998, "status": "recovered",
        "reason": "recovered_via_followup", "last_interaction": _iso(now - timedelta(days=2)),
    })

    # ---- Recommendation events ----
    for _ in range(8):
        await db.recommendation_events.insert_one({
            "merchant_id": merchant_id, "kind": "cross_sell", "accepted": True, "revenue": 2499})
    for _ in range(12):
        await db.recommendation_events.insert_one({
            "merchant_id": merchant_id, "kind": "cross_sell", "accepted": False, "revenue": 0})
    for _ in range(5):
        await db.recommendation_events.insert_one({
            "merchant_id": merchant_id, "kind": "upsell", "accepted": True, "revenue": 3499})

    # ---- Templates, rules, knowledge ----
    await db.whatsapp_templates.insert_one({
        "merchant_id": merchant_id, "name": "order_confirmation", "use_case": "order_confirmation",
        "language": "en", "category": "UTILITY",
        "body": "Hi {{1}}, your order {{2}} is confirmed. Total Rs. {{3}}. Thank you!",
        "variables": ["name", "order_number", "total"], "meta_template_id": None,
        "approval_status": "PENDING", "created_at": _iso(now),
    })
    await db.business_rules.insert_one({
        "merchant_id": merchant_id, "instruction": "For Lahore delivery, tell customers 1-2 working days.",
        "category": "shipping", "active": True, "created_at": _iso(now),
    })
    await db.knowledge_documents.insert_one({
        "merchant_id": merchant_id, "title": "Return Policy", "category": "returns",
        "content": "Returns accepted within 7 days of delivery for unused items with tags intact.",
        "created_at": _iso(now),
    })


async def write_test_credentials():
    email = os.environ.get("ADMIN_EMAIL", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    content = f"""# Test Credentials

## Owner / Merchant Account (JWT email-password auth)
- Email: {email}
- Password: {password}
- Role: owner
- Store: TechGear Threads (seeded demo tenant)

## Auth endpoints
- POST /api/auth/register  (creates merchant + owner)
- POST /api/auth/login
- POST /api/auth/logout
- GET  /api/auth/me
- POST /api/auth/refresh

## Notes
- Login returns user + access_token (also set as httpOnly cookies).
- Frontend also sends Authorization: Bearer <access_token>.
- Shopify / WhatsApp / LLM are NOT connected (foundation build) by design.
"""
    from pathlib import Path
    Path("/app/memory").mkdir(parents=True, exist_ok=True)
    Path("/app/memory/test_credentials.md").write_text(content)

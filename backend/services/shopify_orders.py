"""Tenant-scoped Shopify order creation and customer cancellation workflows."""
import re
from datetime import datetime, timezone

from bson import ObjectId

from database import db
from services.shopify_service import ShopifyAPIError, ShopifyNotConfigured, shopify_service

CANCEL_PATTERN = re.compile(
    r"\b(cancel|cancellation|cancelled|cancelled order|cancel order|cancel kar|cancel kr|cancel kardo|order nahi chahiye|order band)\b",
    re.IGNORECASE,
)


def is_cancellation_request(text: str) -> bool:
    return bool(CANCEL_PATTERN.search(text or ""))


async def _resolve_items(merchant_id: ObjectId, items: list[dict]) -> tuple[list[dict], list[dict], float, str | None]:
    line_items = []
    local_items = []
    total = 0.0
    error = None
    for item in items:
        product = await db.products.find_one({
            "merchant_id": merchant_id,
            "title": {"$regex": re.escape(item.get("product", "")), "$options": "i"},
        })
        if not product:
            error = f"Product not found: {item.get('product', '')}"
            continue
        query = {"merchant_id": merchant_id, "product_id": product["_id"]}
        if item.get("size"):
            query["size"] = {"$regex": f"^{re.escape(item['size'])}$", "$options": "i"}
        if item.get("color"):
            query["color"] = {"$regex": f"^{re.escape(item['color'])}$", "$options": "i"}
        variant = await db.product_variants.find_one(query)
        if not variant or not variant.get("shopify_variant_id"):
            error = f"Shopify variant is not synced for: {product['title']}"
            continue
        quantity = max(1, int(item.get("quantity", 1)))
        price = float(variant.get("price", product.get("price", 0)))
        line_items.append({"variant_id": int(variant["shopify_variant_id"]), "quantity": quantity})
        local_items.append({"title": product["title"], "variant": item.get("size") or item.get("color"), "qty": quantity, "price": price})
        total += price * quantity
    return line_items, local_items, total, error


async def create_customer_order(merchant_id: str, conversation_id: str | None, args: dict) -> dict:
    oid = ObjectId(merchant_id)
    connection = await db.shopify_connections.find_one({"merchant_id": oid})
    if not connection or not connection.get("access_token_enc") or not shopify_service.configured:
        return {"created": False, "code": "shopify_not_connected", "message": "Shopify is not connected. Do not claim that the order was placed."}
    items = args.get("items") or []
    line_items, local_items, subtotal, error = await _resolve_items(oid, items)
    if error or not line_items:
        return {"created": False, "code": "catalog_not_synced", "message": error or "No valid Shopify-synced items were found."}
    phone = args.get("customer_phone") or ""
    customer = await db.customers.find_one({"merchant_id": oid, "phone": phone})
    if not customer:
        inserted = await db.customers.insert_one({
            "merchant_id": oid, "name": args.get("customer_name") or phone, "phone": phone,
            "preferred_language": None, "total_orders": 0, "delivered_orders": 0,
            "cancelled_orders": 0, "refused_orders": 0, "failed_deliveries": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        customer = {"_id": inserted.inserted_id}
    shipping_address = {
        "name": args.get("customer_name") or phone,
        "phone": phone,
        "address1": args.get("address") or "",
        "city": args.get("city") or "",
        "country": "Pakistan",
    }
    payload = {
        "line_items": line_items,
        "financial_status": "pending",
        "shipping_address": shipping_address,
        "note": "Created by Chatify AI from WhatsApp",
        "tags": "chatify-ai",
        "send_receipt": False,
        "send_fulfillment_receipt": False,
        "note_attributes": [{"name": "chatify_conversation_id", "value": conversation_id or ""}],
    }
    try:
        shopify_order = await shopify_service.create_order(
            connection["shop_domain"], connection["access_token_enc"], payload)
    except (ShopifyNotConfigured, ShopifyAPIError) as exc:
        return {"created": False, "code": "shopify_order_failed", "message": str(exc)}
    now = datetime.now(timezone.utc).isoformat()
    total = float(shopify_order.get("total_price") or subtotal)
    existing = await db.shopify_orders.find_one({"merchant_id": oid, "shopify_order_id": str(shopify_order.get("id"))})
    if existing:
        if conversation_id:
            await db.shopify_orders.update_one({"_id": existing["_id"], "merchant_id": oid}, {"$set": {"conversation_id": ObjectId(conversation_id), "shopify_synced_at": now}})
        return {"created": True, "order_id": str(existing["_id"]), "shopify_order_id": str(shopify_order.get("id")), "order_number": existing.get("order_number"), "total": existing.get("total", total), "currency": "PKR", "payment_method": existing.get("payment_method", args.get("payment_method", "COD"))}
    local = {
        "merchant_id": oid, "customer_id": customer["_id"],
        "order_number": shopify_order.get("order_number") or shopify_order.get("name") or shopify_order.get("id"),
        "shopify_order_id": str(shopify_order.get("id")), "total": total,
        "attribution": "AI", "payment_method": args.get("payment_method", "COD"),
        "payment_status": "unpaid", "fulfillment_status": "pending",
        "items": local_items, "conversation_id": ObjectId(conversation_id) if conversation_id else None,
        "created_at": now, "shopify_synced_at": now,
    }
    inserted = await db.shopify_orders.insert_one(local)
    if conversation_id:
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id), "merchant_id": oid},
            {"$set": {"conversation_value": total, "ai_resolved": True, "last_order_id": inserted.inserted_id}},
        )
    return {"created": True, "order_id": str(inserted.inserted_id), "shopify_order_id": str(shopify_order.get("id")), "order_number": local["order_number"], "total": total, "currency": "PKR", "payment_method": local["payment_method"]}


async def cancel_latest_customer_order(merchant_id: str, customer_phone: str) -> dict:
    oid = ObjectId(merchant_id)
    customer = await db.customers.find_one({"merchant_id": oid, "phone": customer_phone})
    if not customer:
        return {"cancelled": False, "code": "order_not_found", "message": "No active order was found."}
    order = await db.shopify_orders.find_one(
        {"merchant_id": oid, "customer_id": customer["_id"], "fulfillment_status": {"$nin": ["cancelled", "cancelled_by_customer", "delivered"]}},
        sort=[("created_at", -1)],
    )
    if not order:
        return {"cancelled": False, "code": "order_not_found", "message": "No active order was found."}
    connection = await db.shopify_connections.find_one({"merchant_id": oid})
    if not connection or not order.get("shopify_order_id"):
        await db.shopify_orders.update_one({"_id": order["_id"], "merchant_id": oid}, {"$set": {"cancellation_status": "pending_shopify", "cancellation_requested_at": datetime.now(timezone.utc).isoformat()}})
        return {"cancelled": False, "code": "shopify_not_connected", "message": "The cancellation request was saved for seller review."}
    try:
        shopify_order = await shopify_service.cancel_order(
            connection["shop_domain"], connection["access_token_enc"], order["shopify_order_id"])
    except (ShopifyNotConfigured, ShopifyAPIError) as exc:
        await db.shopify_orders.update_one({"_id": order["_id"], "merchant_id": oid}, {"$set": {"cancellation_status": "shopify_failed", "cancellation_error": str(exc)[:240]}})
        return {"cancelled": False, "code": "shopify_cancel_failed", "message": "Shopify could not cancel the order yet. A seller will review it."}
    now = datetime.now(timezone.utc).isoformat()
    await db.shopify_orders.update_one(
        {"_id": order["_id"], "merchant_id": oid},
        {"$set": {"fulfillment_status": "cancelled_by_customer", "cancellation_status": "cancelled_on_shopify", "cancelled_at": now, "shopify_cancelled_at": now}},
    )
    await db.customers.update_one({"_id": customer["_id"], "merchant_id": oid}, {"$inc": {"cancelled_orders": 1}})
    return {"cancelled": True, "order_number": order.get("order_number"), "shopify_order_id": str(shopify_order.get("id") or order["shopify_order_id"])}

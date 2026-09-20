"""Tenant-scoped Shopify order creation and customer cancellation workflows."""
import re
from datetime import datetime, timezone

from bson import ObjectId

from database import db
from services.shopify_service import (
    ShopifyAPIError,
    ShopifyNotConfigured,
    shopify_service,
)

CANCEL_PATTERN = re.compile(
    r"\b(cancel|cancellation|cancelled|cancelled order|cancel order|cancel kar|cancel kr|cancel kardo|order nahi chahiye|order band)\b",
    re.IGNORECASE,
)


def is_cancellation_request(text: str) -> bool:
    return bool(CANCEL_PATTERN.search(text or ""))


async def _resolve_items(merchant_id: ObjectId, items: list[dict]) -> tuple[list[dict], list[dict], float, str | None]:
    """Resolve an order against Chatify's local catalog first.

    Shopify variant IDs are optional metadata used only for the external sync. A
    locally maintained product/variant is enough to create the merchant's order.
    """
    line_items = []
    local_items = []
    total = 0.0
    missing_product = None
    for item in items:
        product = await db.products.find_one({
            "merchant_id": merchant_id,
            "title": {"$regex": re.escape(item.get("product", "")), "$options": "i"},
        })
        if not product:
            missing_product = f"Product not found: {item.get('product', '')}"
            continue
        query = {"merchant_id": merchant_id, "product_id": product["_id"]}
        if item.get("size"):
            query["size"] = {"$regex": f"^{re.escape(item['size'])}$", "$options": "i"}
        if item.get("color"):
            query["color"] = {"$regex": f"^{re.escape(item['color'])}$", "$options": "i"}
        variant = await db.product_variants.find_one(query)
        quantity = max(1, int(item.get("quantity", 1)))
        price = float((variant or {}).get("price", product.get("price", 0)))
        local_items.append({
            "title": product["title"],
            "variant": item.get("size") or item.get("color"),
            "qty": quantity,
            "price": price,
            "product_id": str(product["_id"]),
            "shopify_variant_id": str(variant["shopify_variant_id"]) if variant and variant.get("shopify_variant_id") else None,
        })
        total += price * quantity
        if variant and variant.get("shopify_variant_id"):
            line_items.append({"variant_id": int(variant["shopify_variant_id"]), "quantity": quantity})
    return line_items, local_items, total, missing_product


async def create_customer_order(merchant_id: str, conversation_id: str | None, args: dict) -> dict:
    """Create the authoritative local order, then best-effort sync it to Shopify.

    Shopify is an optional channel. It must never prevent a confirmed order from
    appearing in Chatify AI or change the customer-facing success result.
    """
    oid = ObjectId(merchant_id)
    items = args.get("items") or []
    line_items, local_items, subtotal, error = await _resolve_items(oid, items)
    if error or not local_items:
        return {"created": False, "code": "catalog_item_missing", "message": "I could not identify one or more items in the store catalog."}

    phone = args.get("customer_phone") or ""
    customer = await db.customers.find_one({"merchant_id": oid, "phone": phone})
    now = datetime.now(timezone.utc).isoformat()
    if not customer:
        inserted = await db.customers.insert_one({
            "merchant_id": oid, "name": args.get("customer_name") or phone, "phone": phone,
            "preferred_language": None, "total_orders": 0, "delivered_orders": 0,
            "cancelled_orders": 0, "refused_orders": 0, "failed_deliveries": 0,
            "created_at": now,
        })
        customer = {"_id": inserted.inserted_id}

    shipping_address = {
        "name": args.get("customer_name") or phone, "phone": phone,
        "address1": args.get("address") or "", "city": args.get("city") or "", "country": "Pakistan",
    }
    local_id = ObjectId()
    local_total = subtotal + (200 if local_items else 0)
    local = {
        "_id": local_id, "merchant_id": oid, "customer_id": customer["_id"],
        "order_number": f"CFY-{str(local_id)[-8:].upper()}", "shopify_order_id": None,
        "total": local_total, "attribution": "AI", "payment_method": args.get("payment_method", "COD"),
        "payment_status": "unpaid", "fulfillment_status": "pending", "sync_status": "local_only",
        "items": local_items, "shipping_address": shipping_address,
        "conversation_id": ObjectId(conversation_id) if conversation_id else None,
        "created_at": now,
    }
    await db.shopify_orders.insert_one(local)
    await db.customers.update_one({"_id": customer["_id"], "merchant_id": oid}, {"$inc": {"total_orders": 1}})

    connection = await db.shopify_connections.find_one({"merchant_id": oid})
    shopify_synced = False
    if connection and connection.get("access_token_enc") and len(line_items) == len(local_items):
        payload = {
            "line_items": line_items, "financial_status": "pending", "shipping_address": shipping_address,
            "note": "Created by Chatify AI from WhatsApp", "tags": "chatify-ai",
            "send_receipt": False, "send_fulfillment_receipt": False,
            "note_attributes": [{"name": "chatify_conversation_id", "value": conversation_id or ""}],
        }
        try:
            shopify_order = await shopify_service.create_order(connection["shop_domain"], connection["access_token_enc"], payload)
            shopify_id = str(shopify_order.get("id"))
            synced_total = float(shopify_order.get("total_price") or local_total)
            await db.shopify_orders.update_one(
                {"_id": local_id, "merchant_id": oid},
                {"$set": {"shopify_order_id": shopify_id,
                          "order_number": shopify_order.get("order_number") or shopify_order.get("name") or local["order_number"],
                          "total": synced_total, "sync_status": "synced", "shopify_synced_at": now}})
            local["order_number"] = shopify_order.get("order_number") or shopify_order.get("name") or local["order_number"]
            local["total"] = synced_total
            local["shopify_order_id"] = shopify_id
            shopify_synced = True
        except (ShopifyNotConfigured, ShopifyAPIError) as exc:
            await db.shopify_orders.update_one({"_id": local_id, "merchant_id": oid},
                                               {"$set": {"sync_status": "sync_pending", "sync_error": str(exc)[:240]}})
    elif connection and connection.get("access_token_enc"):
        await db.shopify_orders.update_one({"_id": local_id, "merchant_id": oid},
                                           {"$set": {"sync_status": "sync_pending", "sync_error": "Catalog variant sync is required."}})

    if conversation_id:
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id), "merchant_id": oid},
            {"$set": {"conversation_value": local["total"], "ai_resolved": True, "last_order_id": local_id}},
        )
    return {"created": True, "order_id": str(local_id), "order_number": local["order_number"],
            "total": local["total"], "currency": "PKR", "payment_method": local["payment_method"],
            "shopify_synced": shopify_synced}


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

    # The local order is authoritative and is cancelled immediately.
    now = datetime.now(timezone.utc).isoformat()
    await db.shopify_orders.update_one(
        {"_id": order["_id"], "merchant_id": oid},
        {"$set": {"fulfillment_status": "cancelled_by_customer", "cancellation_status": "cancelled_locally",
                  "cancellation_requested_at": now, "cancelled_at": now}})
    await db.customers.update_one({"_id": customer["_id"], "merchant_id": oid}, {"$inc": {"cancelled_orders": 1}})

    connection = await db.shopify_connections.find_one({"merchant_id": oid})
    if connection and connection.get("access_token_enc") and order.get("shopify_order_id"):
        try:
            shopify_order = await shopify_service.cancel_order(
                connection["shop_domain"], connection["access_token_enc"], order["shopify_order_id"])
            await db.shopify_orders.update_one(
                {"_id": order["_id"], "merchant_id": oid},
                {"$set": {"cancellation_status": "cancelled_on_shopify", "shopify_cancelled_at": now}})
            return {"cancelled": True, "order_number": order.get("order_number"),
                    "shopify_synced": True, "shopify_order_id": str(shopify_order.get("id") or order["shopify_order_id"])}
        except (ShopifyNotConfigured, ShopifyAPIError) as exc:
            await db.shopify_orders.update_one(
                {"_id": order["_id"], "merchant_id": oid},
                {"$set": {"cancellation_status": "sync_pending", "cancellation_error": str(exc)[:240]}})

    return {"cancelled": True, "order_number": order.get("order_number"), "shopify_synced": False}

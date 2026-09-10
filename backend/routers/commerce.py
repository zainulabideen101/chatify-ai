"""Catalog (products, media), customers, orders, abandoned orders, COD, address tool."""
from pathlib import Path
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from database import db, serialize, serialize_list
from deps import get_tenant_id
from models import MediaCreate, PaymentReviewDecision, iso_now
from services.commerce import address_validator, cod_assessor
from services.whatsapp_service import whatsapp_service
from services.shopify_service import ShopifyAPIError, ShopifyNotConfigured, shopify_service
from audit import log_action

router = APIRouter(prefix="/api", tags=["commerce"])




def _safe_order(doc):
    result = serialize(doc)
    result.pop("payment_screenshot_path", None)
    return result
def _oid(tid): return ObjectId(tid)


# ---------- Products ----------
@router.get("/products")
async def list_products(tid: str = Depends(get_tenant_id), q: str = None, limit: int = 100):
    query = {"merchant_id": _oid(tid)}
    if q:
        query["title"] = {"$regex": q, "$options": "i"}
    docs = await db.products.find(query).limit(limit).to_list(limit)
    return serialize_list(docs)


@router.get("/products/{product_id}")
async def get_product(product_id: str, tid: str = Depends(get_tenant_id)):
    doc = await db.products.find_one({"_id": ObjectId(product_id), "merchant_id": _oid(tid)})
    if not doc:
        raise HTTPException(404, "Product not found")
    variants = await db.product_variants.find(
        {"product_id": ObjectId(product_id), "merchant_id": _oid(tid)}).to_list(200)
    media = await db.product_media.find(
        {"product_id": ObjectId(product_id), "merchant_id": _oid(tid)}).to_list(200)
    return {**serialize(doc), "variants": serialize_list(variants), "media": serialize_list(media)}


@router.get("/product-relationships")
async def list_relationships(tid: str = Depends(get_tenant_id)):
    docs = await db.product_relationships.find({"merchant_id": _oid(tid)}).to_list(500)
    return serialize_list(docs)


# ---------- Media library ----------
@router.get("/media")
async def list_media(tid: str = Depends(get_tenant_id), tag: str = None):
    query = {"merchant_id": _oid(tid)}
    if tag:
        query["tags"] = tag
    docs = await db.product_media.find(query).sort("created_at", -1).to_list(500)
    return serialize_list(docs)


@router.post("/media")
async def create_media(payload: MediaCreate, tid: str = Depends(get_tenant_id)):
    doc = {
        "merchant_id": _oid(tid), "filename": payload.filename, "media_type": payload.media_type,
        "url": payload.url,
        "product_id": ObjectId(payload.product_id) if payload.product_id else None,
        "variant_id": ObjectId(payload.variant_id) if payload.variant_id else None,
        "color": payload.color, "size": payload.size, "tags": payload.tags,
        "description": payload.description, "created_at": iso_now(),
    }
    res = await db.product_media.insert_one(doc)
    await log_action(tid, "media_uploaded", actor="merchant", detail={"filename": payload.filename})
    return serialize({**doc, "_id": res.inserted_id})


@router.delete("/media/{media_id}")
async def delete_media(media_id: str, tid: str = Depends(get_tenant_id)):
    await db.product_media.delete_one({"_id": ObjectId(media_id), "merchant_id": _oid(tid)})
    return {"ok": True}


# ---------- Customers ----------
@router.get("/customers")
async def list_customers(tid: str = Depends(get_tenant_id), limit: int = 100):
    docs = await db.customers.find({"merchant_id": _oid(tid)}).limit(limit).to_list(limit)
    return serialize_list(docs)


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str, tid: str = Depends(get_tenant_id)):
    doc = await db.customers.find_one({"_id": ObjectId(customer_id), "merchant_id": _oid(tid)})
    if not doc:
        raise HTTPException(404, "Customer not found")
    memory = await db.customer_memory.find_one({"customer_id": ObjectId(customer_id)})
    orders = await db.shopify_orders.find(
        {"customer_id": ObjectId(customer_id), "merchant_id": _oid(tid)}).to_list(100)
    cod = cod_assessor.assess({
        "previous_orders": doc.get("total_orders", 0),
        "delivered_orders": doc.get("delivered_orders", 0),
        "cancelled_orders": doc.get("cancelled_orders", 0),
        "refused_orders": doc.get("refused_orders", 0),
        "failed_deliveries": doc.get("failed_deliveries", 0),
    })
    return {**serialize(doc), "memory": serialize(memory),
            "orders": serialize_list(orders), "cod_risk": cod}


# ---------- Orders ----------
@router.get("/orders")
async def list_orders(tid: str = Depends(get_tenant_id), limit: int = 100):
    docs = await db.shopify_orders.find({"merchant_id": _oid(tid)}).sort("created_at", -1).limit(limit).to_list(limit)
    return [_safe_order(doc) for doc in docs]


@router.get("/orders/{order_id}")
async def get_order(order_id: str, tid: str = Depends(get_tenant_id)):
    doc = await db.shopify_orders.find_one({"_id": ObjectId(order_id), "merchant_id": _oid(tid)})
    if not doc:
        raise HTTPException(404, "Order not found")
    return _safe_order(doc)



@router.get("/payment-review")
async def payment_review_queue(tid: str = Depends(get_tenant_id)):
    docs = await db.shopify_orders.find({
        "merchant_id": _oid(tid), "payment_verification_status": {"$in": ["review_required", "ai_marked_paid_screenshot"]},
    }).sort("payment_verified_at", -1).limit(200).to_list(200)
    return [_safe_order(doc) for doc in docs]


@router.get("/orders/{order_id}/payment-evidence")
async def payment_evidence(order_id: str, tid: str = Depends(get_tenant_id)):
    doc = await db.shopify_orders.find_one({"_id": ObjectId(order_id), "merchant_id": _oid(tid)})
    if not doc or not doc.get("payment_screenshot_path"):
        raise HTTPException(404, "Payment screenshot not found")
    root = Path("/app/backend/uploads/payment_screenshots").resolve()
    evidence = Path(doc["payment_screenshot_path"]).resolve()
    if root not in evidence.parents or not evidence.is_file():
        raise HTTPException(404, "Payment screenshot not found")
    return FileResponse(evidence, media_type=doc.get("payment_screenshot_mime_type", "application/octet-stream"))


@router.post("/orders/{order_id}/payment-review")
async def review_payment(order_id: str, payload: PaymentReviewDecision, tid: str = Depends(get_tenant_id)):
    oid = _oid(tid)
    order = await db.shopify_orders.find_one({"_id": ObjectId(order_id), "merchant_id": oid})
    if not order:
        raise HTTPException(404, "Order not found")
    if order.get("payment_verification_status") not in {"review_required", "ai_marked_paid_screenshot"}:
        raise HTTPException(400, "This order is not waiting for payment review")
    now = iso_now()
    approved = payload.decision == "approve"
    update = {
        "payment_verification_status": "human_approved_screenshot" if approved else "payment_rejected",
        "payment_verification_label": "Human approved payment screenshot" if approved else "Payment screenshot rejected",
        "payment_verified_by": "human",
        "payment_reviewed_by": tid,
        "payment_reviewed_at": now,
        "payment_review_note": payload.note,
        "payment_status": "paid" if approved else "unpaid",
    }
    if approved:
        update["payment_method"] = "BANK_TRANSFER"
    await db.shopify_orders.update_one({"_id": order["_id"], "merchant_id": oid}, {"$set": update})
    if order.get("conversation_id"):
        await db.conversations.update_one(
            {"_id": order["conversation_id"], "merchant_id": oid},
            {"$set": {"payment_review_status": "approved" if approved else "rejected", "needs_attention": False}},
        )
    # Update the connected Shopify order without blocking the local review decision.
    shopify_sync = "not_available"
    shopify_sync_error = None
    connection = await db.shopify_connections.find_one({"merchant_id": oid})
    if connection and connection.get("access_token_enc") and order.get("shopify_order_id") and shopify_service.configured:
        try:
            await shopify_service.update_payment_review(
                connection["shop_domain"], connection["access_token_enc"],
                order["shopify_order_id"], approved, float(order.get("total") or 0),
                payload.note or "Payment screenshot reviewed by seller.")
            shopify_sync = "synced"
        except (ShopifyNotConfigured, ShopifyAPIError) as exc:
            shopify_sync = "sync_pending"
            shopify_sync_error = str(exc)[:240]
    elif connection and order.get("shopify_order_id"):
        shopify_sync = "not_configured"
    await db.shopify_orders.update_one(
        {"_id": order["_id"], "merchant_id": oid},
        {"$set": {"shopify_payment_sync_status": shopify_sync, "shopify_payment_sync_error": shopify_sync_error}},
    )

    customer = await db.customers.find_one({"_id": order.get("customer_id"), "merchant_id": oid})
    conversation = None
    if order.get("conversation_id"):
        conversation = await db.conversations.find_one({"_id": order["conversation_id"], "merchant_id": oid})
    if not conversation and customer:
        conversation = await db.conversations.find_one({"merchant_id": oid, "customer_id": customer["_id"]}, sort=[("last_message_at", -1)])
    notification = "not_sent"
    notification_text = (
        f"Your payment for order #{order.get('order_number')} has been verified and your order is now being processed."
        if approved else
        "We could not verify the payment screenshot for your order. Please send a clearer screenshot or contact us for help."
    )
    if connection and customer and customer.get("phone"):
        notification_message = {
            "conversation_id": conversation["_id"] if conversation else None,
            "merchant_id": oid, "direction": "outbound", "sender": "human",
            "body": notification_text, "status": "failed", "created_at": now,
        }
        try:
            sent = await whatsapp_service.send_text(connection, customer["phone"], notification_text)
            notification = "sent"
            notification_message.update({"status": "sent", "whatsapp_message_id": ((sent.get("messages") or [{}])[0]).get("id")})
        except Exception as exc:
            notification = "failed"
            notification_message["delivery_error"] = str(exc)[:500]
        if conversation:
            await db.messages.insert_one(notification_message)
    await log_action(tid, "payment_review_decided", actor="human", resource_id=order_id,
                     detail={"decision": payload.decision, "notification": notification, "shopify_sync": shopify_sync})
    updated = await db.shopify_orders.find_one({"_id": order["_id"], "merchant_id": oid})
    return {"order": _safe_order(updated), "notification": notification, "shopify_sync": shopify_sync}

# ---------- Abandoned orders ----------
@router.get("/abandoned-orders")
async def list_abandoned(tid: str = Depends(get_tenant_id)):
    docs = await db.abandoned_orders.find({"merchant_id": _oid(tid)}).sort("last_interaction", -1).to_list(200)
    return serialize_list(docs)


# ---------- Address validation tool (deterministic backend) ----------
@router.post("/tools/validate-address")
async def validate_address(payload: dict, tid: str = Depends(get_tenant_id)):
    return address_validator.validate(payload.get("address", ""), payload.get("city"))


# ---------- COD risk tool ----------
@router.post("/tools/cod-risk")
async def cod_risk(payload: dict, tid: str = Depends(get_tenant_id)):
    return cod_assessor.assess(payload)

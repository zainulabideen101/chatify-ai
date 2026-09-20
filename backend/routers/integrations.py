"""Integration connection architecture: Shopify OAuth, WhatsApp/Meta connect, webhooks."""
import hashlib
import json
import logging
import os
import secrets

from bson import ObjectId
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from pydantic import BaseModel, Field

from audit import log_action
from database import db
from deps import get_tenant_id
from models import ShopifyConnectRequest, WhatsAppConnectRequest, iso_now
from services.billing import record_unique_chat
from services.crypto import decrypt_secret, encrypt_secret
from services.meta_whatsapp import MetaGraphError, meta_whatsapp_service
from services.shopify_service import ShopifyAPIError, ShopifyNotConfigured, shopify_service
from services.whatsapp_service import whatsapp_service

logger = logging.getLogger(__name__)



class EmbeddedSignupRequest(BaseModel):
    code: str = Field(min_length=10, max_length=4096)
    waba_id: str | None = None
    phone_number_id: str | None = None


def _meta_http_error(exc: MetaGraphError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.public_detail())



async def _ensure_tenant_asset_available(merchant_id: ObjectId, details: dict):
    conflict = await db.whatsapp_connections.find_one({
        "merchant_id": {"$ne": merchant_id},
        "status": {"$in": ["connected", "connecting"]},
        "$or": [
            {"waba_id": details["waba_id"]},
            {"phone_number_id": details["phone_number_id"]},
        ],
    })
    if conflict:
        raise HTTPException(status_code=409, detail="This WhatsApp Business Account or phone number is already connected to another merchant.")

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("/status")
async def integrations_status(tid: str = Depends(get_tenant_id)):
    shopify = await db.shopify_connections.find_one({"merchant_id": ObjectId(tid)})
    whatsapp = await db.whatsapp_connections.find_one({"merchant_id": ObjectId(tid)})
    product_count = await db.products.count_documents({"merchant_id": ObjectId(tid)})
    variant_count = await db.product_variants.count_documents({"merchant_id": ObjectId(tid)})
    return {
        "shopify": {
            "connected": bool(shopify),
            "shop_domain": shopify.get("shop_domain") if shopify else None,
            "shop_name": shopify.get("shop_name") if shopify else None,
            "last_sync": shopify.get("last_sync") if shopify else None,
            "sync_errors": shopify.get("sync_errors", 0) if shopify else 0,
            "connection_type": shopify.get("connection_type", "manual") if shopify else None,
            "server_configured": True,
            "products": product_count,
            "variants": variant_count,
        },
        "whatsapp": {
            "connected": bool(whatsapp and whatsapp.get("status", "connected") == "connected"),
            "status": whatsapp.get("status", "connected") if whatsapp else "not_connected",
            "display_phone_number": whatsapp.get("display_phone_number") if whatsapp else None,
            "waba_id": whatsapp.get("waba_id") if whatsapp else None,
            "waba_name": whatsapp.get("waba_name") if whatsapp else None,
            "phone_number_id": whatsapp.get("phone_number_id") if whatsapp else None,
            "phone_status": whatsapp.get("phone_status") if whatsapp else None,
            "quality_rating": whatsapp.get("quality_rating") if whatsapp else None,
            "last_error": whatsapp.get("last_error") if whatsapp else None,
            "server_configured": meta_whatsapp_service.app_configured,
            "webhook_configured": meta_whatsapp_service.webhook_configured,
            "graph_version": meta_whatsapp_service.graph_version,
        },
    }


# ---------- Shopify (per-merchant Client ID/Secret, client-credentials grant) ----------
@router.post("/shopify/connect")
async def shopify_connect(payload: ShopifyConnectRequest, request: Request, tid: str = Depends(get_tenant_id)):
    """Connect directly with the merchant's Shopify app Client ID and Client Secret.

    No OAuth redirect: the credentials are exchanged server-side via Shopify's
    client-credentials grant for a 24h Admin API access token, which is then
    refreshed automatically whenever it expires. The store and the app must
    belong to the same organization in the Shopify Dev Dashboard.
    """
    shop_domain = (payload.shop_domain or "").strip().lower()
    client_id = (payload.client_id or "").strip()
    client_secret = (payload.client_secret or "").strip()
    if not shop_domain.endswith(".myshopify.com"):
        raise HTTPException(status_code=400, detail="Enter your <store>.myshopify.com domain.")
    if len(client_id) < 10 or len(client_secret) < 10:
        raise HTTPException(status_code=400, detail="Enter the Client ID and Client Secret from your Shopify app settings.")

    try:
        creds = await shopify_service.exchange_client_credentials(shop_domain, client_id, client_secret)
        shop_info = await shopify_service.verify_admin_token(shop_domain, creds["access_token"])
    except ShopifyAPIError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from datetime import datetime, timedelta, timezone
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=creds["expires_in"])).isoformat()
    # Auto-register order + fulfillment webhooks so new activity syncs without a button press.
    backend = str(request.base_url).rstrip("/")
    if backend.startswith("http://") and "localhost" not in backend and "127.0.0.1" not in backend:
        backend = "https://" + backend[len("http://"):]
    callback_url = f"{backend}/api/webhooks/shopify"
    webhook_result = {"created": 0, "already_present": 0}
    try:
        webhook_result = await shopify_service.register_webhooks(shop_domain, creds["access_token"], callback_url)
    except Exception:
        pass
    await db.shopify_connections.update_one(
        {"merchant_id": ObjectId(tid)},
        {"$set": {
            "merchant_id": ObjectId(tid),
            "shop_domain": shop_domain,
            "client_id_enc": encrypt_secret(client_id),
            "client_secret_enc": encrypt_secret(client_secret),
            "webhook_secret_enc": encrypt_secret(client_secret),
            "access_token_enc": encrypt_secret(creds["access_token"]),
            "token_expires_at": expires_at,
            "connection_type": "client_credentials",
            "shop_name": shop_info.get("name"),
            "shop_email": shop_info.get("email"),
            "connected_at": iso_now(),
            "webhook_callback_url": callback_url,
            "webhooks_registered": webhook_result.get("created", 0) + webhook_result.get("already_present", 0),
            "last_sync": None, "sync_errors": 0,
        }},
        upsert=True,
    )
    await log_action(tid, "shopify_connected", actor="merchant",
                     detail={"shop": shop_domain, "type": "client_credentials", **webhook_result})
    return {"ok": True, "shop_domain": shop_domain, "shop_name": shop_info.get("name"),
            "webhooks": webhook_result}


@router.post("/shopify/disconnect")
async def shopify_disconnect(tid: str = Depends(get_tenant_id)):
    await db.shopify_connections.delete_one({"merchant_id": ObjectId(tid)})
    await log_action(tid, "shopify_disconnected", actor="merchant")
    return {"ok": True}


@router.post("/shopify/sync")
async def shopify_sync(tid: str = Depends(get_tenant_id)):
    conn = await db.shopify_connections.find_one({"merchant_id": ObjectId(tid)})
    if not conn:
        raise HTTPException(status_code=400, detail="Connect a Shopify store first")
    try:
        token = await shopify_service.ensure_access_token(conn)
        result = await shopify_service.sync_catalog(conn["shop_domain"], token, tid)
        await log_action(tid, "shopify_catalog_synced", actor="merchant", detail=result)
        return result
    except ShopifyNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ShopifyAPIError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/shopify/sync-orders")
async def shopify_sync_orders(tid: str = Depends(get_tenant_id)):
    conn = await db.shopify_connections.find_one({"merchant_id": ObjectId(tid)})
    if not conn:
        raise HTTPException(status_code=400, detail="Connect a Shopify store first")
    try:
        token = await shopify_service.ensure_access_token(conn)
        result = await shopify_service.sync_orders(conn["shop_domain"], token, tid)
        await log_action(tid, "shopify_orders_synced", actor="merchant", detail=result)
        return result
    except ShopifyNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ShopifyAPIError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- WhatsApp / Meta ----------
@router.post("/whatsapp/connect")
async def whatsapp_connect(payload: WhatsAppConnectRequest, tid: str = Depends(get_tenant_id)):
    oid = ObjectId(tid)
    await db.whatsapp_connections.update_one(
        {"merchant_id": oid},
        {"$set": {"merchant_id": oid, "status": "connecting", "last_error": None, "updated_at": iso_now()}},
        upsert=True,
    )
    try:
        details = await meta_whatsapp_service.validate_connection(
            payload.access_token, payload.waba_id, payload.phone_number_id, subscribe=True)
        await _ensure_tenant_asset_available(oid, details)
    except MetaGraphError as exc:
        await db.whatsapp_connections.update_one(
            {"merchant_id": oid},
            {"$set": {"status": "error", "last_error": exc.public_detail(), "updated_at": iso_now()}},
        )
        raise _meta_http_error(exc)
    await db.whatsapp_connections.update_one(
        {"merchant_id": oid},
        {"$set": {"merchant_id": oid, **details,
                  "access_token_enc": encrypt_secret(payload.access_token),
                  "status": "connected", "connected_at": iso_now(),
                  "onboarding": "manual", "last_error": None, "updated_at": iso_now()}},
        upsert=True,
    )
    await log_action(tid, "whatsapp_connected", actor="merchant",
                     detail={"phone": details.get("display_phone_number"), "waba_id": details.get("waba_id")})
    return {"ok": True, "status": "connected", **{k: v for k, v in details.items() if k != "token_expires_at"}}



@router.post("/whatsapp/disconnect")
async def whatsapp_disconnect(tid: str = Depends(get_tenant_id)):
    oid = ObjectId(tid)
    conversation_ids = [doc["_id"] async for doc in db.conversations.find({"merchant_id": oid}, {"_id": 1})]
    await db.messages.delete_many({"merchant_id": oid})
    await db.conversations.delete_many({"merchant_id": oid})
    await db.whatsapp_connections.delete_one({"merchant_id": oid})
    await log_action(tid, "whatsapp_disconnected", actor="merchant", detail={"conversations_removed": len(conversation_ids)})
    return {"ok": True, "conversations_removed": len(conversation_ids)}


@router.get("/whatsapp/embedded-config")
async def whatsapp_embedded_config(tid: str = Depends(get_tenant_id)):
    """Public-to-the-authenticated-merchant config needed by Meta's SDK."""
    return {
        "app_id": meta_whatsapp_service.app_id,
        "config_id": os.environ.get("META_CONFIG_ID", ""),
        "graph_version": meta_whatsapp_service.graph_version,
        "configured": meta_whatsapp_service.app_configured and bool(os.environ.get("META_CONFIG_ID")),
        "webhook_configured": meta_whatsapp_service.webhook_configured,
    }


@router.post("/whatsapp/embedded-exchange")
async def whatsapp_embedded_exchange(payload: EmbeddedSignupRequest, tid: str = Depends(get_tenant_id)):
    """Exchange a short-lived Embedded Signup code only on the server."""
    if not meta_whatsapp_service.app_configured or not os.environ.get("META_CONFIG_ID"):
        raise HTTPException(status_code=503, detail="Embedded Signup needs META_APP_ID, META_APP_SECRET, and META_CONFIG_ID on the server.")
    oid = ObjectId(tid)
    await db.whatsapp_connections.update_one(
        {"merchant_id": oid},
        {"$set": {"merchant_id": oid, "status": "connecting", "last_error": None, "updated_at": iso_now()}},
        upsert=True,
    )
    try:
        token = await meta_whatsapp_service.exchange_code(payload.code)
        details = await meta_whatsapp_service.validate_connection(
            token, payload.waba_id, payload.phone_number_id, subscribe=True)
        await _ensure_tenant_asset_available(oid, details)
    except MetaGraphError as exc:
        await db.whatsapp_connections.update_one(
            {"merchant_id": oid},
            {"$set": {"status": "error", "last_error": exc.public_detail(), "updated_at": iso_now()}},
        )
        raise _meta_http_error(exc)
    await db.whatsapp_connections.update_one(
        {"merchant_id": oid},
        {"$set": {"merchant_id": oid, **details,
                  "access_token_enc": encrypt_secret(token),
                  "status": "connected", "connected_at": iso_now(),
                  "onboarding": "embedded_signup", "last_error": None, "updated_at": iso_now()}},
        upsert=True,
    )
    await log_action(tid, "whatsapp_connected_embedded", actor="merchant", detail={"waba_id": details.get("waba_id")})
    return {"status": "connected", **{k: v for k, v in details.items() if k != "token_expires_at"}}


@router.post("/whatsapp/validate")
async def whatsapp_validate(tid: str = Depends(get_tenant_id)):
    oid = ObjectId(tid)
    connection = await db.whatsapp_connections.find_one({"merchant_id": oid})
    if not connection or not connection.get("access_token_enc"):
        raise HTTPException(status_code=404, detail="WhatsApp is not connected.")
    try:
        token = decrypt_secret(connection["access_token_enc"])
        details = await meta_whatsapp_service.validate_connection(
            token, connection.get("waba_id"), connection.get("phone_number_id"), subscribe=True)
    except MetaGraphError as exc:
        await db.whatsapp_connections.update_one(
            {"merchant_id": oid},
            {"$set": {"status": "error", "last_error": exc.public_detail(), "updated_at": iso_now()}},
        )
        raise _meta_http_error(exc)
    await db.whatsapp_connections.update_one(
        {"merchant_id": oid},
        {"$set": {**details, "status": "connected", "last_error": None, "validated_at": iso_now(), "updated_at": iso_now()}},
    )
    return {"status": "connected", **{k: v for k, v in details.items() if k != "token_expires_at"}}



# ---------- Public webhooks (no auth) ----------
webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@webhook_router.get("/whatsapp")
async def whatsapp_verify(request: Request):
    p = request.query_params
    challenge = whatsapp_service.verify_webhook_subscription(
        p.get("hub.mode"), p.get("hub.verify_token"), p.get("hub.challenge"))
    if challenge is None:
        raise HTTPException(status_code=403, detail="Verification failed")
    return Response(content=challenge, media_type="text/plain")


async def _process_whatsapp_inbound(payload: dict):
    """Parse a WhatsApp Cloud API webhook, store the message, and let the AI reply
    when the conversation is AI_ACTIVE and both WhatsApp + LLM are connected.
    """
    from services.conversation_ai import generate_and_send_ai_reply
    from services.payment_verification import verify_payment_screenshot
    from services.shopify_orders import (
        cancel_latest_customer_order,
        is_cancellation_request,
    )
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
                if not phone_number_id:
                    continue
                conn = await db.whatsapp_connections.find_one({"phone_number_id": phone_number_id})
                if not conn:
                    continue
                mid = conn["merchant_id"]
                if entry.get("id") and str(conn.get("waba_id")) != str(entry.get("id")):
                    continue
                for status_event in value.get("statuses", []):
                    status_id = status_event.get("id")
                    if status_id:
                        await db.messages.update_many(
                            {"merchant_id": mid, "whatsapp_message_id": status_id},
                            {"$set": {"status": status_event.get("status", "unknown"), "status_errors": status_event.get("errors"), "status_timestamp": status_event.get("timestamp")}})
                for msg in value.get("messages", []):
                    from_phone = msg.get("from")
                    message_type = msg.get("type")
                    text = (msg.get("text") or {}).get("body", "")
                    image_payload = msg.get("image") if message_type == "image" else None
                    if not text and not image_payload:
                        continue
                    message_body = text or "[Payment screenshot received]"
                    message_id = msg.get("id")
                    if message_id and await db.messages.find_one({"merchant_id": mid, "whatsapp_message_id": message_id}, {"_id": 1}):
                        continue
                    contact = next((item for item in value.get("contacts", []) if item.get("wa_id") == from_phone), None)
                    whatsapp_name = ((contact or {}).get("profile") or {}).get("name") or from_phone
                    profile_picture_url = ((contact or {}).get("profile") or {}).get("picture")
                    customer = await db.customers.find_one({"merchant_id": mid, "phone": from_phone})
                    if not customer:
                        cid_ins = await db.customers.insert_one({
                            "merchant_id": mid, "name": whatsapp_name, "whatsapp_name": whatsapp_name,
                            "profile_picture_url": profile_picture_url, "phone": from_phone,
                            "preferred_language": None, "total_orders": 0, "delivered_orders": 0,
                            "cancelled_orders": 0, "refused_orders": 0, "failed_deliveries": 0,
                            "created_at": iso_now()})
                        customer = {"_id": cid_ins.inserted_id, "name": whatsapp_name, "whatsapp_name": whatsapp_name,
                                    "profile_picture_url": profile_picture_url}
                    else:
                        customer_update = {"whatsapp_name": whatsapp_name}
                        if profile_picture_url:
                            customer_update["profile_picture_url"] = profile_picture_url
                        await db.customers.update_one({"_id": customer["_id"], "merchant_id": mid}, {"$set": customer_update})
                    await db.abandoned_orders.update_many(
                        {"merchant_id": mid, "customer_id": customer["_id"], "status": "abandoned"},
                        {"$set": {"last_customer_reply_at": iso_now()}})
                    conv = await db.conversations.find_one({"merchant_id": mid, "customer_phone": from_phone})
                    if not conv:
                        conv_ins = await db.conversations.insert_one({
                            "merchant_id": mid, "customer_id": customer["_id"],
                            "customer_name": whatsapp_name, "whatsapp_name": whatsapp_name,
                            "profile_picture_url": profile_picture_url, "customer_phone": from_phone,
                            "mode": "AI_ACTIVE", "needs_attention": False, "ai_resolved": False,
                            "low_confidence": False, "had_human_takeover": False,
                            "conversation_value": 0, "intent": "OTHER",
                            "last_message": message_body, "last_message_at": iso_now(), "created_at": iso_now()})
                        conv = await db.conversations.find_one({"_id": conv_ins.inserted_id})
                    await db.conversations.update_one(
                        {"_id": conv["_id"], "merchant_id": mid},
                        {"$set": {"customer_name": whatsapp_name, "whatsapp_name": whatsapp_name,
                                  "profile_picture_url": profile_picture_url, "customer_phone": from_phone}})
                    await db.messages.insert_one({
                        "conversation_id": conv["_id"], "merchant_id": mid, "direction": "inbound",
                        "sender": "customer", "body": message_body, "status": "received", "message_type": message_type,
                        "whatsapp_message_id": msg.get("id"), "whatsapp_media_id": (image_payload or {}).get("id"), "created_at": iso_now()})
                    await db.conversations.update_one({"_id": conv["_id"]},
                        {"$set": {"last_message": message_body, "last_message_at": iso_now(),
                                  "last_inbound_at": iso_now()},
                         "$inc": {"unread_count": 1}})

                    usage = await record_unique_chat(str(mid), from_phone)
                    if text and is_cancellation_request(text):
                        cancellation = await cancel_latest_customer_order(str(mid), from_phone)
                        if cancellation.get("cancelled"):
                            customer_reply = "Your order has been cancelled successfully."
                        elif cancellation.get("code") == "order_not_found":
                            customer_reply = "I could not find an active order to cancel. Please share your order number so we can check it."
                        else:
                            customer_reply = "I have received your cancellation request. Please share your order number so we can check it."
                        try:
                            send_result = await whatsapp_service.send_text(conn, from_phone, customer_reply)
                            await db.messages.insert_one({
                                "conversation_id": conv["_id"], "merchant_id": mid, "direction": "outbound",
                                "sender": "ai", "body": customer_reply, "status": "sent",
                                "whatsapp_message_id": ((send_result.get("messages") or [{}])[0]).get("id"),
                                "shopify_cancellation": cancellation.get("code") or "cancelled", "created_at": iso_now()})
                        except Exception:
                            logger.exception("Cancellation response delivery failed for conversation %s", conv["_id"])
                        continue
                    if image_payload:
                        # Payment evidence must be processed even during human takeover or
                        # chat-limit pauses; it belongs to the seller's review queue.
                        try:
                            image_bytes, mime_type = await whatsapp_service.download_media(conn, image_payload.get("id"))
                            payment_result = await verify_payment_screenshot(
                                str(mid), str(conv["_id"]), image_bytes, mime_type, image_payload.get("id"))
                            customer_reply = payment_result.get("customer_message", "Thanks, we received your payment screenshot. Our team will review the payment proof shortly.")
                            send_result = await whatsapp_service.send_text(conn, from_phone, customer_reply)
                            await db.messages.insert_one({
                                "conversation_id": conv["_id"], "merchant_id": mid, "direction": "outbound",
                                "sender": "ai", "body": customer_reply, "status": "sent",
                                "whatsapp_message_id": ((send_result.get("messages") or [{}])[0]).get("id"),
                                "payment_verification": payment_result.get("decision"), "created_at": iso_now()})
                        except Exception as exc:
                            logger.exception("Payment screenshot processing failed for conversation %s", conv["_id"])
                            await db.conversations.update_one(
                                {"_id": conv["_id"], "merchant_id": mid},
                                {"$set": {"needs_attention": True, "payment_review_status": "review_required", "payment_review_error": str(exc)[:240]}})
                        continue
                    if conv.get("mode") != "AI_ACTIVE":
                        continue
                    if not usage["ai_allowed"]:
                        await db.conversations.update_one(
                            {"_id": conv["_id"], "merchant_id": mid},
                            {"$set": {"needs_attention": True, "ai_paused_reason": "daily_chat_limit"}})
                        continue
                    try:
                        await generate_and_send_ai_reply(mid, conv)
                    except Exception:
                        logger.exception("AI reply processing failed for conversation %s", conv["_id"])
                        await db.conversations.update_one(
                            {"_id": conv["_id"], "merchant_id": mid},
                            {"$set": {"needs_attention": True, "ai_reply_error": "AI reply could not be delivered."}})
    except Exception:
        logger.exception("WhatsApp webhook processing failed")


@webhook_router.post("/whatsapp")
async def whatsapp_incoming(request: Request, background: BackgroundTasks):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not meta_whatsapp_service.verify_signature(body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:

        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    if payload.get("object") not in (None, "whatsapp_business_account"):
        return {"received": True}
    event_key = hashlib.sha256(body).hexdigest()
    inserted = await db.webhook_events.update_one(
        {"source": "whatsapp", "event_key": event_key},
        {"$setOnInsert": {"source": "whatsapp", "event_key": event_key, "payload": payload, "received_at": iso_now()}},
        upsert=True,
    )
    if not inserted.upserted_id:
        return {"received": True, "duplicate": True}
    background.add_task(_process_whatsapp_inbound, payload)
    return {"received": True}



async def _sync_shopify_order_event(shop_domain: str, topic: str, payload: dict):
    connection = await db.shopify_connections.find_one({"shop_domain": shop_domain})
    if not connection or not payload.get("id"):
        return
    mid = connection["merchant_id"]
    shopify_id = str(payload["id"])
    now = iso_now()
    financial_status = payload.get("financial_status")
    fulfillment_status = payload.get("fulfillment_status")
    updates = {"shopify_order_id": shopify_id, "shopify_synced_at": now}
    if financial_status == "paid" or topic.endswith("/paid"):
        updates["payment_status"] = "paid"
    elif financial_status:
        updates["payment_status"] = "unpaid"
    if topic.endswith("/cancelled"):
        updates.update({"fulfillment_status": "cancelled", "cancellation_status": "cancelled_on_shopify", "cancelled_at": now})
    elif fulfillment_status:
        updates["fulfillment_status"] = fulfillment_status
    local = await db.shopify_orders.find_one({"merchant_id": mid, "shopify_order_id": shopify_id})
    if local:
        await db.shopify_orders.update_one({"_id": local["_id"], "merchant_id": mid}, {"$set": updates})
        return
    shipping = payload.get("shipping_address") or {}
    customer_data = payload.get("customer") or {}
    phone = shipping.get("phone") or customer_data.get("phone") or ""
    customer_name = (
        shipping.get("name")
        or " ".join(filter(None, [customer_data.get("first_name"), customer_data.get("last_name")])).strip()
        or None
    )
    updates["customer_name"] = customer_name
    updates["shipping_address"] = {
        "name": shipping.get("name"), "address1": shipping.get("address1"),
        "address2": shipping.get("address2"), "city": shipping.get("city"),
        "province": shipping.get("province"), "zip": shipping.get("zip"),
        "country": shipping.get("country"), "phone": phone,
    }
    updates["delivery_address"] = ", ".join(filter(None, [
        shipping.get("address1"), shipping.get("address2"), shipping.get("city"),
        shipping.get("province"), shipping.get("zip"), shipping.get("country"),
    ])) or None
    updates["order_date"] = payload.get("created_at") or now
    fulfillments = [
        {
            "tracking_number": f.get("tracking_number"),
            "tracking_company": f.get("tracking_company"),
            "tracking_url": f.get("tracking_url"),
            "shipment_status": f.get("shipment_status"),
            "dispatched_at": f.get("created_at"),
        }
        for f in (payload.get("fulfillments") or [])
        if f.get("status") != "cancelled"
    ]
    updates["fulfillments"] = fulfillments
    updates["dispatched"] = bool(fulfillments)
    updates["dispatched_at"] = fulfillments[0]["dispatched_at"] if fulfillments else None
    updates["customer_phone"] = "".join(ch for ch in (phone or "") if ch.isdigit()) or None
    customer = await db.customers.find_one({"merchant_id": mid, "phone": phone}) if phone else None
    if not customer:
        inserted = await db.customers.insert_one({
            "merchant_id": mid, "name": shipping.get("name") or customer_data.get("first_name") or phone,
            "phone": phone, "preferred_language": None, "total_orders": 0,
            "delivered_orders": 0, "cancelled_orders": 0, "refused_orders": 0,
            "failed_deliveries": 0, "created_at": now,
        })
        customer = {"_id": inserted.inserted_id}
    if not fulfillment_status:
        updates["fulfillment_status"] = "pending"
    updates.update({
        "merchant_id": mid, "customer_id": customer["_id"],
        "order_number": payload.get("order_number") or payload.get("name") or shopify_id,
        "total": float(payload.get("total_price") or 0), "payment_method": "SHOPIFY",
        "attribution": "SHOPIFY", "items": [
            {"title": item.get("title") or item.get("name"), "variant": item.get("variant_title"),
             "qty": item.get("quantity", 1), "price": float(item.get("price") or 0)}
            for item in payload.get("line_items", [])
        ], "created_at": payload.get("created_at") or now,
    })
    await db.shopify_orders.insert_one(updates)
    # Keep only the 100 most recent local orders per merchant to stay lightweight.
    cutoff = await db.shopify_orders.find(
        {"merchant_id": mid}).sort("created_at", -1).skip(100).to_list(1)
    if cutoff:
        await db.shopify_orders.delete_many(
            {"merchant_id": mid, "created_at": {"$lte": cutoff[0].get("created_at") or ""}}
        )



@webhook_router.post("/shopify")
async def shopify_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
    shop_domain = request.headers.get("X-Shopify-Shop-Domain", "").lower()
    # Manual-mode webhook verification uses a per-connection secret set by the merchant.
    # If the merchant hasn't provided one, we accept the payload (they may still be
    # configuring webhooks) but tag the record for review.
    connection = await db.shopify_connections.find_one({"shop_domain": shop_domain})
    secret_enc = connection.get("webhook_secret_enc") if connection else None
    verified = False
    if secret_enc:
        try:
            secret = decrypt_secret(secret_enc)
            verified = shopify_service.verify_webhook(body, hmac_header, secret)
        except Exception:
            verified = False
        if not verified:
            raise HTTPException(status_code=401, detail="Invalid HMAC")
    topic = request.headers.get("X-Shopify-Topic", "unknown")
    payload = await request.json()
    event_key = hashlib.sha256(body).hexdigest()
    inserted = await db.webhook_events.update_one(
        {"source": "shopify", "event_key": event_key},
        {"$setOnInsert": {"source": "shopify", "event_key": event_key, "topic": topic, "shop_domain": shop_domain, "payload": payload, "received_at": iso_now()}},
        upsert=True,
    )
    if inserted.upserted_id:
        background.add_task(_sync_shopify_order_event, shop_domain, topic, payload)
    return {"received": True}

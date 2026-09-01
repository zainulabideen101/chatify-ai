"""Integration connection architecture: Shopify OAuth, WhatsApp/Meta connect, webhooks."""
import hashlib
import json
import os
import secrets
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks
from pydantic import BaseModel, Field

from database import db, serialize
from deps import get_tenant_id
from models import ShopifyConnectRequest, WhatsAppConnectRequest, iso_now
from services.shopify_service import shopify_service, ShopifyNotConfigured
from services.whatsapp_service import whatsapp_service
from services.meta_whatsapp import MetaGraphError, meta_whatsapp_service
from services.crypto import encrypt_secret, decrypt_secret
from audit import log_action
from services.billing import record_unique_chat


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
            "last_sync": shopify.get("last_sync") if shopify else None,
            "sync_errors": shopify.get("sync_errors", 0) if shopify else 0,
            "server_configured": shopify_service.configured,
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


# ---------- Shopify ----------
@router.post("/shopify/connect")
async def shopify_connect(payload: ShopifyConnectRequest, request: Request, tid: str = Depends(get_tenant_id)):
    state = secrets.token_urlsafe(24)
    await db.oauth_states.insert_one({
        "state": state, "merchant_id": ObjectId(tid),
        "shop_domain": payload.shop_domain, "created_at": iso_now(),
    })
    backend = str(request.base_url).rstrip("/")
    redirect_uri = f"{backend}/api/integrations/shopify/callback"
    try:
        url = shopify_service.build_authorize_url(payload.shop_domain, redirect_uri, state)
    except ShopifyNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    await log_action(tid, "shopify_connect_initiated", actor="merchant", detail={"shop": payload.shop_domain})
    return {"authorize_url": url}


@router.get("/shopify/callback")
async def shopify_callback(request: Request):
    params = dict(request.query_params)
    if not shopify_service.verify_hmac(params):
        raise HTTPException(status_code=400, detail="Invalid HMAC / Shopify not configured")
    st = await db.oauth_states.find_one({"state": params.get("state")})
    if not st:
        raise HTTPException(status_code=400, detail="Invalid state")
    token = await shopify_service.exchange_code(params["shop"], params["code"])
    await db.shopify_connections.update_one(
        {"merchant_id": st["merchant_id"]},
        {"$set": {"merchant_id": st["merchant_id"], "shop_domain": params["shop"],
                  "access_token_enc": encrypt_secret(token), "connected_at": iso_now(),
                  "last_sync": None, "sync_errors": 0}},
        upsert=True,
    )
    await log_action(str(st["merchant_id"]), "shopify_connected", actor="merchant",
                     detail={"shop": params["shop"]})
    return {"ok": True, "shop": params["shop"]}


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
        result = await shopify_service.sync_catalog(conn["shop_domain"], conn.get("access_token_enc"), tid)
        await log_action(tid, "shopify_catalog_synced", actor="merchant", detail=result)
        return result
    except ShopifyNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))


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
    await db.whatsapp_connections.delete_one({"merchant_id": ObjectId(tid)})
    await log_action(tid, "whatsapp_disconnected", actor="merchant")
    return {"ok": True}


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
    from services.ai_agent import ai_agent, LLMNotConfigured
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
                    text = (msg.get("text") or {}).get("body", "")
                    if not text:
                        continue
                    customer = await db.customers.find_one({"merchant_id": mid, "phone": from_phone})
                    if not customer:
                        cid_ins = await db.customers.insert_one({
                            "merchant_id": mid, "name": from_phone, "phone": from_phone,
                            "preferred_language": None, "total_orders": 0, "delivered_orders": 0,
                            "cancelled_orders": 0, "refused_orders": 0, "failed_deliveries": 0,
                            "created_at": iso_now()})
                        customer = {"_id": cid_ins.inserted_id, "name": from_phone}
                    await db.abandoned_orders.update_many(
                        {"merchant_id": mid, "customer_id": customer["_id"], "status": "abandoned"},
                        {"$set": {"last_customer_reply_at": iso_now()}})
                    conv = await db.conversations.find_one({"merchant_id": mid, "customer_phone": from_phone})
                    if not conv:
                        conv_ins = await db.conversations.insert_one({
                            "merchant_id": mid, "customer_id": customer["_id"],
                            "customer_name": customer.get("name"), "customer_phone": from_phone,
                            "mode": "AI_ACTIVE", "needs_attention": False, "ai_resolved": False,
                            "low_confidence": False, "had_human_takeover": False,
                            "conversation_value": 0, "intent": "OTHER",
                            "last_message": text, "last_message_at": iso_now(), "created_at": iso_now()})
                        conv = await db.conversations.find_one({"_id": conv_ins.inserted_id})
                    await db.messages.insert_one({
                        "conversation_id": conv["_id"], "merchant_id": mid, "direction": "inbound",
                        "sender": "customer", "body": text, "status": "received", "created_at": iso_now()})
                    await db.conversations.update_one({"_id": conv["_id"]},
                        {"$set": {"last_message": text, "last_message_at": iso_now()}})

                    usage = await record_unique_chat(str(mid), from_phone)
                    if conv.get("mode") != "AI_ACTIVE" or not ai_agent.llm_connected:
                        continue
                    if not usage["ai_allowed"]:
                        await db.conversations.update_one(
                            {"_id": conv["_id"], "merchant_id": mid},
                            {"$set": {"needs_attention": True, "ai_paused_reason": "daily_chat_limit"}})
                        continue
                    prior = await db.messages.find({"conversation_id": conv["_id"]}).sort("created_at", 1).to_list(50)
                    history = [{"role": "user" if m["direction"] == "inbound" else "assistant",
                                "content": m["body"]} for m in prior[:-1]]
                    try:
                        result = await ai_agent.run_agent(str(mid), text, history=history,
                                                          conversation_id=str(conv["_id"]))
                    except LLMNotConfigured:
                        continue
                    reply = result["reply"]
                    if reply:
                        try:
                            send_result = await whatsapp_service.send_text(conn, from_phone, reply)
                            status = "sent"
                            whatsapp_message_id = ((send_result.get("messages") or [{}])[0]).get("id")
                        except Exception:
                            status = "queued"
                            whatsapp_message_id = None
                        await db.messages.insert_one({
                            "conversation_id": conv["_id"], "merchant_id": mid, "direction": "outbound",
                            "sender": "ai", "body": reply, "status": status,
                            "whatsapp_message_id": whatsapp_message_id,
                            "tool_trace": result.get("tool_trace"), "created_at": iso_now()})
                        await db.conversations.update_one({"_id": conv["_id"]},
                            {"$set": {"last_message": reply, "last_message_at": iso_now()}})
    except Exception:
        pass


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



@webhook_router.post("/shopify")
async def shopify_webhook(request: Request):
    body = await request.body()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
    if not shopify_service.verify_webhook(body, hmac_header):
        raise HTTPException(status_code=401, detail="Invalid HMAC")
    topic = request.headers.get("X-Shopify-Topic", "unknown")
    payload = await request.json()
    await db.webhook_events.insert_one({"source": "shopify", "topic": topic,
                                        "payload": payload, "received_at": iso_now()})
    return {"received": True}

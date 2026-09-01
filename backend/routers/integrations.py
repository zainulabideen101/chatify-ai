"""Integration connection architecture: Shopify OAuth, WhatsApp/Meta connect, webhooks."""
import secrets
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks

from database import db, serialize
from deps import get_tenant_id
from models import ShopifyConnectRequest, WhatsAppConnectRequest, iso_now
from services.shopify_service import shopify_service, ShopifyNotConfigured
from services.whatsapp_service import whatsapp_service
from services.crypto import encrypt_secret
from audit import log_action
from services.billing import record_unique_chat

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
            "connected": bool(whatsapp),
            "display_phone_number": whatsapp.get("display_phone_number") if whatsapp else None,
            "waba_id": whatsapp.get("waba_id") if whatsapp else None,
            "server_configured": whatsapp_service.app_configured,
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
    await db.whatsapp_connections.update_one(
        {"merchant_id": ObjectId(tid)},
        {"$set": {"merchant_id": ObjectId(tid), "waba_id": payload.waba_id,
                  "phone_number_id": payload.phone_number_id,
                  "display_phone_number": payload.display_phone_number,
                  "access_token_enc": encrypt_secret(payload.access_token),
                  "connected_at": iso_now()}},
        upsert=True,
    )
    await log_action(tid, "whatsapp_connected", actor="merchant",
                     detail={"phone": payload.display_phone_number})
    return {"ok": True}


@router.post("/whatsapp/disconnect")
async def whatsapp_disconnect(tid: str = Depends(get_tenant_id)):
    await db.whatsapp_connections.delete_one({"merchant_id": ObjectId(tid)})
    await log_action(tid, "whatsapp_disconnected", actor="merchant")
    return {"ok": True}


@router.get("/whatsapp/embedded-config")
async def whatsapp_embedded_config(tid: str = Depends(get_tenant_id)):
    """Config the frontend needs to launch Meta Embedded Signup (FB.login)."""
    import os
    return {
        "app_id": os.environ.get("META_APP_ID", ""),
        "config_id": os.environ.get("META_CONFIG_ID", ""),
        "graph_version": whatsapp_service.GRAPH_VERSION,
        "configured": whatsapp_service.app_configured and bool(os.environ.get("META_CONFIG_ID")),
    }


@router.post("/whatsapp/embedded-exchange")
async def whatsapp_embedded_exchange(payload: dict, tid: str = Depends(get_tenant_id)):
    """Exchange the Embedded Signup authorization code for a business token and
    register the merchant's WABA/phone. Requires META_APP_ID/SECRET on the server.
    """
    import os
    import requests
    if not whatsapp_service.app_configured:
        raise HTTPException(status_code=503, detail="Meta App credentials (META_APP_ID/META_APP_SECRET) not configured on server.")
    code = payload.get("code")
    waba_id = payload.get("waba_id")
    phone_number_id = payload.get("phone_number_id")
    if not code or not waba_id or not phone_number_id:
        raise HTTPException(status_code=400, detail="code, waba_id and phone_number_id are required")
    gv = whatsapp_service.GRAPH_VERSION
    resp = requests.get(
        f"https://graph.facebook.com/{gv}/oauth/access_token",
        params={"client_id": os.environ["META_APP_ID"],
                "client_secret": os.environ["META_APP_SECRET"], "code": code},
        timeout=20)
    resp.raise_for_status()
    token = resp.json()["access_token"]
    display = payload.get("display_phone_number", "")
    await db.whatsapp_connections.update_one(
        {"merchant_id": ObjectId(tid)},
        {"$set": {"merchant_id": ObjectId(tid), "waba_id": waba_id,
                  "phone_number_id": phone_number_id, "display_phone_number": display,
                  "access_token_enc": encrypt_secret(token), "connected_at": iso_now(),
                  "onboarding": "embedded_signup"}},
        upsert=True)
    await log_action(tid, "whatsapp_connected_embedded", actor="merchant", detail={"waba_id": waba_id})
    return {"ok": True}


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
                            await whatsapp_service.send_text(conn, from_phone, reply)
                            status = "sent"
                        except Exception:
                            status = "queued"
                        await db.messages.insert_one({
                            "conversation_id": conv["_id"], "merchant_id": mid, "direction": "outbound",
                            "sender": "ai", "body": reply, "status": status,
                            "tool_trace": result.get("tool_trace"), "created_at": iso_now()})
                        await db.conversations.update_one({"_id": conv["_id"]},
                            {"$set": {"last_message": reply, "last_message_at": iso_now()}})
    except Exception:
        pass


@webhook_router.post("/whatsapp")
async def whatsapp_incoming(request: Request, background: BackgroundTasks):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not whatsapp_service.verify_signature(body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")
    payload = await request.json()
    await db.webhook_events.insert_one({"source": "whatsapp", "payload": payload, "received_at": iso_now()})
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

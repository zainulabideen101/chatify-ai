"""Integration connection architecture: Shopify OAuth, WhatsApp/Meta connect, webhooks."""
import secrets
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from database import db, serialize
from deps import get_tenant_id
from models import ShopifyConnectRequest, WhatsAppConnectRequest, iso_now
from services.shopify_service import shopify_service, ShopifyNotConfigured
from services.whatsapp_service import whatsapp_service
from services.crypto import encrypt_secret
from audit import log_action

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
        result = await shopify_service.sync_catalog(conn["shop_domain"], conn.get("access_token_enc"))
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


@webhook_router.post("/whatsapp")
async def whatsapp_incoming(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not whatsapp_service.verify_signature(body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")
    payload = await request.json()
    await db.webhook_events.insert_one({"source": "whatsapp", "payload": payload, "received_at": iso_now()})
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

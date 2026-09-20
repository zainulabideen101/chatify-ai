"""Conversation inbox: list, thread, human takeover, return-to-AI, summary, suggestions."""
import re
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from database import db, serialize, serialize_list
from deps import get_tenant_id
from models import NewConversationRequest, SendMessageRequest, iso_now
from services.whatsapp_service import whatsapp_service
from services.shopify_service import shopify_service
from services.ai_agent import ai_agent
from services.conversation_ai import generate_and_send_ai_reply
from audit import log_action

router = APIRouter(prefix="/api", tags=["inbox"])


def _oid(tid): return ObjectId(tid)


def _normalize_pk_phone(raw: str) -> str:
    """Store WhatsApp numbers as Meta-compatible digits-only Pakistan E.164 values."""
    value = re.sub(r"[^0-9+]", "", (raw or "").strip())
    if value.startswith("+"):
        value = value[1:]
    if value.startswith("00"):
        value = value[2:]
    if value.startswith("03"):
        value = "92" + value[1:]
    if not re.fullmatch(r"923\d{9}", value):
        raise HTTPException(422, "Enter a valid Pakistani WhatsApp number in +92 3XX XXXXXXX format")
    return value


async def _auto_handover_sweep(mid: ObjectId):
    """Revert HUMAN_ACTIVE conversations to AI when the configured timeout has elapsed
    without any human outbound message since takeover, and an unread customer message exists.
    Runs inline (fast filter + guarded background AI calls) whenever the inbox is polled.
    """
    settings = await db.ai_settings.find_one({"merchant_id": mid}) or {}
    timeout_s = int(settings.get("auto_handover_seconds", 30) or 0)
    if timeout_s <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_s)
    cutoff_iso = cutoff.isoformat()

    convs = await db.conversations.find({
        "merchant_id": mid,
        "mode": "HUMAN_ACTIVE",
        "takeover_at": {"$lt": cutoff_iso},
        "unread_count": {"$gt": 0},
    }).to_list(50)

    for conv in convs:
        takeover_at = conv.get("takeover_at")
        # Defensive: normalise to ISO string if a legacy datetime is present.
        if isinstance(takeover_at, datetime):
            takeover_at = takeover_at.isoformat()
        # Skip if the human replied after takeover.
        last_human = await db.messages.find_one({
            "conversation_id": conv["_id"], "merchant_id": mid,
            "direction": "outbound", "sender": "human",
            "created_at": {"$gt": takeover_at or ""},
        })
        if last_human:
            continue
        await db.conversations.update_one(
            {"_id": conv["_id"], "merchant_id": mid},
            {"$set": {"mode": "AI_ACTIVE", "auto_reverted_at": iso_now()},
             "$unset": {"takeover_at": ""}})
        await log_action(str(mid), "auto_reverted_to_ai", actor="system", conversation_id=str(conv["_id"]))
        try:
            fresh = await db.conversations.find_one({"_id": conv["_id"], "merchant_id": mid})
            await generate_and_send_ai_reply(mid, fresh)
        except Exception:
            pass


@router.get("/conversations")
async def list_conversations(background: BackgroundTasks, tid: str = Depends(get_tenant_id),
                             attention: bool = None, limit: int = 100):
    background.add_task(_auto_handover_sweep, _oid(tid))
    query = {"merchant_id": _oid(tid)}
    if attention:
        query["needs_attention"] = True
    docs = await db.conversations.find(query).sort("last_message_at", -1).limit(limit).to_list(limit)
    return serialize_list(docs)


@router.post("/conversations")
async def create_conversation(payload: NewConversationRequest, tid: str = Depends(get_tenant_id)):
    """Open a seller-started human thread for a Pakistani WhatsApp number."""
    mid = _oid(tid)
    phone = _normalize_pk_phone(payload.phone)
    existing = await db.conversations.find_one({"merchant_id": mid, "customer_phone": phone})
    if existing:
        return {"conversation": serialize(existing), "existing": True}

    customer = await db.customers.find_one({"merchant_id": mid, "phone": phone})
    if not customer:
        customer_insert = await db.customers.insert_one({
            "merchant_id": mid, "name": phone, "whatsapp_name": phone, "phone": phone,
            "preferred_language": None, "total_orders": 0, "delivered_orders": 0,
            "cancelled_orders": 0, "refused_orders": 0, "failed_deliveries": 0,
            "created_at": iso_now(),
        })
        customer = {"_id": customer_insert.inserted_id, "name": phone, "phone": phone}

    now = iso_now()
    conversation = {
        "merchant_id": mid, "customer_id": customer["_id"],
        "customer_name": customer.get("whatsapp_name") or customer.get("name") or phone,
        "whatsapp_name": customer.get("whatsapp_name") or customer.get("name") or phone,
        "customer_phone": phone, "mode": "HUMAN_ACTIVE", "needs_attention": False,
        "ai_resolved": False, "low_confidence": False, "had_human_takeover": True,
        "conversation_value": 0, "intent": "OTHER", "unread_count": 0,
        "last_message": "New conversation", "last_message_at": now,
        "created_at": now, "started_by": "human",
    }
    inserted = await db.conversations.insert_one(conversation)
    conversation["_id"] = inserted.inserted_id
    await log_action(tid, "conversation_created", actor="human", conversation_id=str(inserted.inserted_id), detail={"phone": phone})
    return {"conversation": serialize(conversation), "existing": False}


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: str, tid: str = Depends(get_tenant_id)):
    """Permanently delete one tenant's chat and its messages, preserving CRM records."""
    mid = _oid(tid)
    try:
        conversation_id = ObjectId(cid)
    except Exception:
        raise HTTPException(404, "Conversation not found")
    conversation = await db.conversations.find_one({"_id": conversation_id, "merchant_id": mid})
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    deleted_messages = await db.messages.delete_many({"conversation_id": conversation_id, "merchant_id": mid})
    await db.conversations.delete_one({"_id": conversation_id, "merchant_id": mid})
    await log_action(tid, "conversation_deleted", actor="human", conversation_id=cid,
                     detail={"messages_deleted": deleted_messages.deleted_count})
    return {"ok": True, "conversation_id": cid, "messages_deleted": deleted_messages.deleted_count}


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, tid: str = Depends(get_tenant_id)):
    conv = await db.conversations.find_one({"_id": ObjectId(cid), "merchant_id": _oid(tid)})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = await db.messages.find({"conversation_id": ObjectId(cid), "merchant_id": _oid(tid)}).sort("created_at", 1).to_list(1000)

    # Mark inbound messages as read by human when the thread is viewed.
    unread_ids = [m["_id"] for m in messages if m.get("direction") == "inbound" and not m.get("read_by_human_at")]
    if unread_ids:
        now = iso_now()
        await db.messages.update_many(
            {"_id": {"$in": unread_ids}}, {"$set": {"read_by_human_at": now}})
        for m in messages:
            if m["_id"] in unread_ids:
                m["read_by_human_at"] = now
    if conv.get("unread_count"):
        await db.conversations.update_one({"_id": ObjectId(cid), "merchant_id": _oid(tid)},
                                          {"$set": {"unread_count": 0}})
        conv["unread_count"] = 0

    customer = None
    if conv.get("customer_id"):
        customer = await db.customers.find_one({"_id": conv["customer_id"], "merchant_id": _oid(tid)})

    # Live-fetch the customer's recent orders from Shopify by phone (no local DB
    # lookup), so this stays fresh even when the local Orders page only keeps
    # the 100 most recent orders per merchant.
    mid = _oid(tid)
    phone = conv.get("customer_phone") or (customer or {}).get("phone") or ""
    orders: list = []
    orders_scope_hint = False
    connection = await db.shopify_connections.find_one({"merchant_id": mid})
    if connection and phone:
        try:
            token = await shopify_service.ensure_access_token(connection)
            lookup = await shopify_service.list_orders_by_phone(
                connection["shop_domain"], token, phone, limit=5)
            orders = lookup["orders"]
            orders_scope_hint = lookup["customer_found"] and not orders
        except Exception:
            orders = []
    return {**serialize(conv), "messages": serialize_list(messages),
            "customer": serialize(customer), "orders": orders,
            "orders_scope_hint": orders_scope_hint}


@router.get("/conversations/{cid}/summary")
async def conversation_summary(cid: str, tid: str = Depends(get_tenant_id)):
    conv = await db.conversations.find_one({"_id": ObjectId(cid), "merchant_id": _oid(tid)})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv.get("ai_summary") or {
        "available": ai_agent.llm_connected,
        "note": "AI summary generates once the LLM is connected.",
    }


@router.get("/conversations/{cid}/suggestion")
async def conversation_suggestion(cid: str, tid: str = Depends(get_tenant_id)):
    conv = await db.conversations.find_one({"_id": ObjectId(cid), "merchant_id": _oid(tid)})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if not ai_agent.llm_connected:
        return {"available": False, "note": "AI suggestions generate once the LLM is connected."}
    return conv.get("ai_suggestion") or {"available": True, "suggestion": None}


@router.post("/conversations/{cid}/takeover")
async def takeover(cid: str, tid: str = Depends(get_tenant_id)):
    await db.conversations.update_one(
        {"_id": ObjectId(cid), "merchant_id": _oid(tid)},
        {"$set": {"mode": "HUMAN_ACTIVE", "needs_attention": False,
                  "takeover_at": iso_now()}})
    await log_action(tid, "human_took_over", actor="human", conversation_id=cid)
    return {"ok": True, "mode": "HUMAN_ACTIVE"}


@router.post("/conversations/{cid}/return-to-ai")
async def return_to_ai(cid: str, background: BackgroundTasks, tid: str = Depends(get_tenant_id)):
    mid = _oid(tid)
    conv = await db.conversations.find_one({"_id": ObjectId(cid), "merchant_id": mid})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    await db.conversations.update_one(
        {"_id": ObjectId(cid), "merchant_id": mid},
        {"$set": {"mode": "AI_ACTIVE"}, "$unset": {"takeover_at": ""}})
    await log_action(tid, "returned_to_ai", actor="human", conversation_id=cid)
    # If the last message is from the customer, immediately trigger an AI response
    # so the customer isn't left hanging when the human hands the chat back.
    latest = await db.messages.find({"conversation_id": ObjectId(cid), "merchant_id": mid}) \
                              .sort("created_at", -1).limit(1).to_list(1)
    if latest and latest[0].get("direction") == "inbound":
        fresh = await db.conversations.find_one({"_id": ObjectId(cid), "merchant_id": mid})
        background.add_task(generate_and_send_ai_reply, mid, fresh)
    return {"ok": True, "mode": "AI_ACTIVE"}


@router.post("/conversations/{cid}/messages")
async def send_message(cid: str, payload: SendMessageRequest, tid: str = Depends(get_tenant_id)):
    conv = await db.conversations.find_one({"_id": ObjectId(cid), "merchant_id": _oid(tid)})
    if not conv:
        raise HTTPException(404, "Conversation not found")

    msg = {"conversation_id": ObjectId(cid), "merchant_id": _oid(tid), "direction": "outbound",
           "sender": "human", "body": payload.body, "status": "queued", "created_at": iso_now()}

    conn = await db.whatsapp_connections.find_one({"merchant_id": _oid(tid)})
    delivery = {"delivered": False}
    try:
        if conn and conv.get("customer_phone"):
            send_result = await whatsapp_service.send_text(conn, conv["customer_phone"], payload.body)
            msg["status"] = "sent"
            msg["whatsapp_message_id"] = ((send_result.get("messages") or [{}])[0]).get("id")
            delivery["delivered"] = True
    except Exception as exc:
        error_text = str(exc)[:500]
        msg["status"] = "failed"
        msg["delivery_error"] = error_text
        delivery["error"] = "Delivery failed. Please check the WhatsApp connection."
        if conn and ("401" in error_text or "190" in error_text or "token" in error_text.lower()):
            await db.whatsapp_connections.update_one(
                {"_id": conn["_id"], "merchant_id": _oid(tid)},
                {"$set": {"status": "error", "last_error": error_text[:240], "updated_at": iso_now()}})

    res = await db.messages.insert_one(msg)
    await db.conversations.update_one({"_id": ObjectId(cid)},
                                      {"$set": {"last_message_at": iso_now(), "last_message": payload.body}})
    await log_action(tid, "human_sent_message", actor="human", conversation_id=cid)
    return {"message": serialize({**msg, "_id": res.inserted_id}), "delivery": delivery}


@router.get("/ai/tools")
async def list_ai_tools(tid: str = Depends(get_tenant_id)):
    return {"llm_connected": ai_agent.llm_connected, "tools": ai_agent.tools.list()}

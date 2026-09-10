"""Conversation inbox: list, thread, human takeover, return-to-AI, summary, suggestions."""
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from database import db, serialize, serialize_list
from deps import get_tenant_id
from models import SendMessageRequest, iso_now
from services.whatsapp_service import whatsapp_service
from services.ai_agent import ai_agent
from services.conversation_ai import generate_and_send_ai_reply
from audit import log_action

router = APIRouter(prefix="/api", tags=["inbox"])


def _oid(tid): return ObjectId(tid)


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
    return {**serialize(conv), "messages": serialize_list(messages), "customer": serialize(customer)}


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

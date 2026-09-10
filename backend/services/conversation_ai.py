"""Shared helper to run the AI agent for an existing conversation and persist the reply.

Used by the WhatsApp webhook (auto-reply on inbound), the return-to-AI action, and
the auto-handover cron path. Centralising ensures every entry point uses the same
history-building and sanitisation rules.
"""
from bson import ObjectId

from database import db
from models import iso_now
from services.ai_agent import ai_agent, LLMNotConfigured
from services.whatsapp_service import whatsapp_service


async def generate_and_send_ai_reply(mid: ObjectId, conv: dict) -> dict:
    """Look at the conversation's message history, produce an AI reply for the latest
    inbound customer message, send it via WhatsApp (if connected), and store the
    outbound message. Returns {sent: bool, reply: str | None, reason: str}.
    """
    if not ai_agent.llm_connected:
        return {"sent": False, "reply": None, "reason": "llm_not_connected"}

    prior = await db.messages.find(
        {"conversation_id": conv["_id"], "merchant_id": mid}
    ).sort("created_at", 1).to_list(100)
    if not prior:
        return {"sent": False, "reply": None, "reason": "no_history"}

    latest_inbound = next((m for m in reversed(prior) if m.get("direction") == "inbound"), None)
    if not latest_inbound:
        return {"sent": False, "reply": None, "reason": "no_inbound_to_answer"}

    history = [
        {"role": "user" if m["direction"] == "inbound" else "assistant", "content": m["body"]}
        for m in prior
        if m["_id"] != latest_inbound["_id"]
    ]

    try:
        result = await ai_agent.run_agent(
            str(mid), latest_inbound["body"], history=history,
            conversation_id=str(conv["_id"]))
    except LLMNotConfigured:
        return {"sent": False, "reply": None, "reason": "llm_not_configured"}

    reply = (result.get("reply") or "").strip()
    if not reply:
        return {"sent": False, "reply": None, "reason": "empty_reply"}

    conn = await db.whatsapp_connections.find_one({"merchant_id": mid})
    status = "queued"
    wa_id = None
    if conn and conv.get("customer_phone"):
        try:
            send_result = await whatsapp_service.send_text(conn, conv["customer_phone"], reply)
            status = "sent"
            wa_id = ((send_result.get("messages") or [{}])[0]).get("id")
        except Exception:
            status = "queued"

    await db.messages.insert_one({
        "conversation_id": conv["_id"], "merchant_id": mid, "direction": "outbound",
        "sender": "ai", "body": reply, "status": status,
        "whatsapp_message_id": wa_id,
        "tool_trace": result.get("tool_trace"), "created_at": iso_now(),
    })
    await db.conversations.update_one(
        {"_id": conv["_id"], "merchant_id": mid, "mode": "AI_ACTIVE"},
        {"$set": {"last_message": reply, "last_message_at": iso_now()}})
    # If mode changed to HUMAN_ACTIVE mid-flight, still record last_message so inbox stays consistent.
    await db.conversations.update_one(
        {"_id": conv["_id"], "merchant_id": mid, "mode": {"$ne": "AI_ACTIVE"}},
        {"$set": {"last_message": reply, "last_message_at": iso_now()}})
    return {"sent": status == "sent", "reply": reply, "reason": "ok"}

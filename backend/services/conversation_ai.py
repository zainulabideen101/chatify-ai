"""Shared helper to run the AI agent for an existing conversation and persist the reply.

Used by the WhatsApp webhook (auto-reply on inbound), the return-to-AI action, and
the auto-handover cron path. Centralising ensures every entry point uses the same
history-building and sanitisation rules.
"""
from bson import ObjectId

from database import db
from models import iso_now
from services.ai_agent import LLMNotConfigured, ai_agent, note_sales_refusal
from services.whatsapp_service import whatsapp_service


async def generate_and_send_ai_reply(mid: ObjectId, conv: dict) -> dict:
    """Generate a full-context AI turn and send it as ONE WhatsApp message."""
    if not ai_agent.llm_connected:
        return {"sent": False, "reply": None, "messages": [], "reason": "llm_not_connected"}

    prior = await db.messages.find(
        {"conversation_id": conv["_id"], "merchant_id": mid}
    ).sort("created_at", 1).to_list(100)
    if not prior:
        return {"sent": False, "reply": None, "messages": [], "reason": "no_history"}

    latest_inbound = next((m for m in reversed(prior) if m.get("direction") == "inbound"), None)
    if not latest_inbound:
        return {"sent": False, "reply": None, "messages": [], "reason": "no_inbound_to_answer"}

    await note_sales_refusal(str(mid), str(conv["_id"]), latest_inbound.get("body", ""))
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
        return {"sent": False, "reply": None, "messages": [], "reason": "llm_not_configured"}

    reply = (result.get("reply") or "").strip()
    if not reply:
        return {"sent": False, "reply": None, "messages": [], "reason": "empty_reply"}

    conn = await db.whatsapp_connections.find_one({"merchant_id": mid})
    status = "local_only"
    wa_id = None
    delivery_error = None
    if conn and conv.get("customer_phone"):
        try:
            send_result = await whatsapp_service.send_text(conn, conv["customer_phone"], reply)
            status = "sent"
            wa_id = ((send_result.get("messages") or [{}])[0]).get("id")
        except Exception as exc:
            status = "failed"
            delivery_error = str(exc)[:500]
            if "401" in delivery_error or "190" in delivery_error or "token" in delivery_error.lower():
                await db.whatsapp_connections.update_one(
                    {"_id": conn["_id"], "merchant_id": mid},
                    {"$set": {"status": "error", "last_error": delivery_error[:240], "updated_at": iso_now()}})
    elif not conn:
        delivery_error = "WhatsApp is not connected for this merchant."
    else:
        status = "failed"
        delivery_error = "The customer phone number is missing."

    message_doc = {
        "conversation_id": conv["_id"], "merchant_id": mid, "direction": "outbound",
        "sender": "ai", "body": reply, "status": status,
        "whatsapp_message_id": wa_id,
        "tool_trace": result.get("tool_trace") or [],
        "created_at": iso_now(),
    }
    if delivery_error:
        message_doc["delivery_error"] = delivery_error
    await db.messages.insert_one(message_doc)

    sent_ok = status == "sent"
    conversation_update = {"last_message": reply, "last_message_at": iso_now()}
    if not sent_ok:
        conversation_update.update({"needs_attention": True, "ai_reply_error": delivery_error})
    await db.conversations.update_one(
        {"_id": conv["_id"], "merchant_id": mid}, {"$set": conversation_update})
    return {"sent": sent_ok, "reply": reply,
            "messages": [{"status": status, "whatsapp_message_id": wa_id, "error": delivery_error}],
            "reason": "ok" if sent_ok else "delivery_failed", "delivery_error": delivery_error}

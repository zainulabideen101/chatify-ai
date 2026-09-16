"""Shared helper to run the AI agent for an existing conversation and persist the reply.

Used by the WhatsApp webhook (auto-reply on inbound), the return-to-AI action, and
the auto-handover cron path. Centralising ensures every entry point uses the same
history-building and sanitisation rules.
"""
import json
import logging
from bson import ObjectId

from database import db
from models import iso_now
from services.ai_agent import LLMNotConfigured, ai_agent, note_sales_refusal
from services.conversation_state import detect_latest_turn
from services.whatsapp_service import whatsapp_service

logger = logging.getLogger(__name__)


async def _build_customer_profile(mid: ObjectId, conv: dict) -> str:
    """Long-term memory block: name, city, language, tags, last few orders."""
    customer_id = conv.get("customer_id")
    customer = await db.customers.find_one({"_id": customer_id, "merchant_id": mid}) if customer_id else None
    if not customer:
        return "CUSTOMER_PROFILE:\n(unknown customer)"
    lines = ["CUSTOMER_PROFILE:"]
    name = customer.get("whatsapp_name") or customer.get("name") or conv.get("customer_name") or "Unknown"
    lines.append(f"- Name: {name}")
    if customer.get("phone"):
        lines.append(f"- Phone: {customer['phone']}")
    if customer.get("city"):
        lines.append(f"- City: {customer['city']}")
    if customer.get("preferred_language"):
        lines.append(f"- Language style: {customer['preferred_language']}")
    tags = customer.get("tags") or []
    if customer.get("sales_opt_out"):
        tags = list(tags) + ["opted_out_of_sales"]
    if tags:
        lines.append(f"- Tags: {', '.join(tags)}")
    stats = []
    for key, label in (("total_orders", "total"), ("delivered_orders", "delivered"),
                       ("cancelled_orders", "cancelled"), ("refused_orders", "refused")):
        val = customer.get(key)
        if val:
            stats.append(f"{label}={val}")
    if stats:
        lines.append(f"- Order stats: {', '.join(stats)}")
    recent = await db.shopify_orders.find(
        {"merchant_id": mid, "customer_id": customer["_id"]}
    ).sort("created_at", -1).to_list(3)
    if recent:
        order_lines = []
        for o in recent:
            oid_disp = o.get("order_number") or o.get("shopify_order_id") or str(o.get("_id"))
            status = o.get("status") or o.get("financial_status") or "unknown"
            when = o.get("created_at") or ""
            order_lines.append(f"  * {oid_disp} — {status} ({when})")
        lines.append("- Recent orders:")
        lines.extend(order_lines)
    return "\n".join(lines)


def _format_conversation_block(prior: list[dict], exclude_id) -> str:
    lines = ["CURRENT_CONVERSATION:"]
    turns = [m for m in prior if m["_id"] != exclude_id][-20:]
    if not turns:
        lines.append("(no prior messages in this session)")
        return "\n".join(lines)
    for m in turns:
        role = "customer" if m.get("direction") == "inbound" else "assistant"
        body = (m.get("body") or "").strip()
        if body:
            lines.append(f"- {role}: {body}")
    return "\n".join(lines)


def _format_context_data(state: dict) -> str:
    keep = {k: state.get(k) for k in (
        "intent", "product", "variant", "size", "quantity", "delivery_address",
        "order_number", "order_number_candidate", "pending_confirmation",
        "confirmation_type", "flow_intent",
    ) if state.get(k) not in (None, "", False)}
    return "CONTEXT_DATA:\n" + json.dumps(keep, ensure_ascii=False, indent=2)


async def _build_user_message(mid: ObjectId, conv: dict, prior: list[dict], latest_inbound: dict, state: dict) -> str:
    profile = await _build_customer_profile(mid, conv)
    conversation_block = _format_conversation_block(prior, latest_inbound["_id"])
    context_block = _format_context_data(state)
    user_block = f"USER_MESSAGE:\n{(latest_inbound.get('body') or '').strip()}"
    return "\n\n".join([profile, conversation_block, context_block, user_block])


async def generate_and_send_ai_reply(mid: ObjectId, conv: dict) -> dict:
    """Generate a full-context AI turn and send it as ONE WhatsApp message."""
    if not ai_agent.llm_connected:
        return {"sent": False, "reply": None, "messages": [], "reason": "llm_not_connected"}

    prior = await db.messages.find(
        {"conversation_id": conv["_id"], "merchant_id": mid}
    ).sort("created_at", 1).to_list(100)
    if not prior:
        return {"sent": False, "reply": None, "messages": [], "reason": "no_history"}

    conv = await db.conversations.find_one({"_id": conv["_id"], "merchant_id": mid}) or conv
    latest_inbound = next((m for m in reversed(prior) if m.get("direction") == "inbound"), None)
    if not latest_inbound:
        return {"sent": False, "reply": None, "messages": [], "reason": "no_inbound_to_answer"}

    state_before = conv.get("conversation_state")
    state = await detect_latest_turn(
        str(mid), str(conv["_id"]), str(conv.get("customer_id") or ""),
        latest_inbound.get("body", ""), state_before)
    update = {"conversation_state": state, "intent": state["intent"], "last_detected_intent": state["last_detected_intent"]}
    if state["intent"] == "confirmation_no":
        update["pending_confirmation"] = False
        await db.conversations.update_one(
            {"_id": conv["_id"], "merchant_id": mid},
            {"$set": update, "$unset": {"pending_order": ""}},
        )
    else:
        await db.conversations.update_one(
            {"_id": conv["_id"], "merchant_id": mid}, {"$set": update})
    conv.update(update)
    logger.info(
        "AI turn conversation_id=%s customer_id=%s message=%r intent=%s product=%r size=%r order_number=%r state_before=%s state_after=%s",
        conv["_id"], conv.get("customer_id"), latest_inbound.get("body", ""), state["intent"],
        state.get("product"), state.get("size"), state.get("order_number_candidate") or state.get("order_number"),
        state_before, state,
    )

    await note_sales_refusal(str(mid), str(conv["_id"]), latest_inbound.get("body", ""))
    user_message = await _build_user_message(mid, conv, prior, latest_inbound, state)
    try:
        result = await ai_agent.run_agent(
            str(mid), user_message,
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

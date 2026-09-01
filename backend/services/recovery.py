"""Seller-configurable abandoned-cart recovery settings and scheduler logic."""
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId

from audit import log_action
from database import db, serialize
from services.whatsapp_service import WhatsAppNotConfigured, whatsapp_service

DEFAULT_RECOVERY_SETTINGS = {
    "enabled": True,
    "trigger_delay_minutes": 60,
    "steps": [
        {"delay_minutes": 0, "template_name": "abandoned_order", "language": "en", "discount_percent": 0, "enabled": True},
    ],
    "stop_on_customer_reply": True,
    "stop_on_order_recovered": True,
    "stop_when_seller_pauses": True,
}


def _oid(merchant_id: str | ObjectId):
    return ObjectId(merchant_id) if isinstance(merchant_id, str) else merchant_id


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return fallback


async def ensure_recovery_settings(merchant_id: str | ObjectId) -> dict:
    oid = _oid(merchant_id)
    existing = await db.recovery_settings.find_one({"merchant_id": oid})
    if existing:
        return existing
    ai_settings = await db.ai_settings.find_one({"merchant_id": oid}) or {}
    now = _now().isoformat()
    doc = {
        "merchant_id": oid,
        **DEFAULT_RECOVERY_SETTINGS,
        "trigger_delay_minutes": int(ai_settings.get("abandoned_delay_minutes", 60)),
        "created_at": now,
        "updated_at": now,
    }
    await db.recovery_settings.insert_one(doc)
    return await db.recovery_settings.find_one({"merchant_id": oid})


async def ensure_all_recovery_settings() -> None:
    async for merchant in db.merchants.find({}, {"_id": 1}):
        await ensure_recovery_settings(merchant["_id"])


async def get_recovery_settings(merchant_id: str) -> dict:
    return serialize(await ensure_recovery_settings(merchant_id))


async def save_recovery_settings(merchant_id: str, values: dict) -> dict:
    oid = _oid(merchant_id)
    now = _now().isoformat()
    await db.recovery_settings.update_one(
        {"merchant_id": oid},
        {"$set": {**values, "updated_at": now}, "$setOnInsert": {"merchant_id": oid, "created_at": now}},
        upsert=True,
    )
    await db.ai_settings.update_one(
        {"merchant_id": oid},
        {"$set": {"abandoned_recovery": values["enabled"], "abandoned_delay_minutes": values["trigger_delay_minutes"]}},
    )
    await log_action(merchant_id, "recovery_settings_updated", actor="merchant", detail=values)
    return await get_recovery_settings(merchant_id)


async def _customer_for_abandoned(item: dict):
    customer_id = item.get("customer_id")
    if not customer_id:
        return None
    return await db.customers.find_one({"_id": customer_id, "merchant_id": item["merchant_id"]})


async def _customer_replied_after(item: dict) -> bool:
    if item.get("last_customer_reply_at"):
        return _parse_iso(item["last_customer_reply_at"], _now()) > _parse_iso(item.get("last_interaction"), _now())
    conv = await db.conversations.find_one({
        "merchant_id": item["merchant_id"],
        "customer_id": item.get("customer_id"),
    })
    if not conv:
        return False
    last = await db.messages.find_one(
        {"conversation_id": conv["_id"], "direction": "inbound"},
        sort=[("created_at", -1)],
    )
    if not last:
        return False
    return _parse_iso(last.get("created_at"), _now()) > _parse_iso(item.get("last_interaction"), _now())


def _step_components(step: dict) -> list:
    discount = float(step.get("discount_percent") or 0)
    if discount <= 0:
        return []
    return [{"type": "body", "parameters": [{"type": "text", "text": f"{discount:g}%"}]}]


def _due_at(item: dict, settings: dict, step: dict, now: datetime) -> datetime:
    step_index = int(item.get("recovery_step_index", 0))
    if step_index == 0:
        base = _parse_iso(item.get("last_interaction"), now)
        return base + timedelta(minutes=int(settings.get("trigger_delay_minutes", 60)))
    base = _parse_iso(item.get("recovery_last_sent_at"), now)
    return base + timedelta(minutes=int(step.get("delay_minutes", 60)))


async def process_recovery_run(run_id: str) -> dict:
    if await db.cron_runs.find_one({"run_id": run_id}):
        return {"processed": 0, "skipped": 0, "run_id": run_id}
    now = _now()
    await db.cron_runs.insert_one({"run_id": run_id, "job": "abandoned-recovery", "at": now.isoformat()})
    processed = 0
    skipped = 0
    await ensure_all_recovery_settings()
    async for settings in db.recovery_settings.find({"enabled": True}):
        mid = settings["merchant_id"]
        steps = [s for s in settings.get("steps", []) if s.get("enabled", True)]
        if not steps:
            continue
        pending = db.abandoned_orders.find({"merchant_id": mid, "status": "abandoned"}).limit(500)
        conn = await db.whatsapp_connections.find_one({"merchant_id": mid})
        for item in await pending.to_list(500):
            step_index = int(item.get("recovery_step_index", 0))
            if step_index >= len(steps):
                continue
            if settings.get("stop_on_customer_reply", True) and await _customer_replied_after(item):
                await db.abandoned_orders.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"recovery_status": "stopped", "recovery_stopped_reason": "customer_replied", "recovery_stopped_at": now.isoformat()}},
                )
                skipped += 1
                continue
            step = steps[step_index]
            if _due_at(item, settings, step, now) > now:
                continue
            customer = await _customer_for_abandoned(item)
            phone = item.get("customer_phone") or (customer or {}).get("phone")
            template = await db.whatsapp_templates.find_one({
                "merchant_id": mid,
                "use_case": "abandoned_order",
                "name": step.get("template_name"),
                "approval_status": "APPROVED",
            })
            if not conn or not phone or not template:
                await db.abandoned_orders.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"recovery_status": "waiting_for_connection_or_template", "recovery_last_error": "Approved abandoned-order template and WhatsApp connection are required."}},
                )
                skipped += 1
                continue
            delivered = False
            error = None
            try:
                await whatsapp_service.send_template(
                    conn, phone, template["name"], template.get("language", step.get("language", "en")),
                    components=_step_components(step),
                )
                delivered = True
            except WhatsAppNotConfigured as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
            next_index = step_index + 1
            update = {
                "customer_phone": phone,
                "recovery_step_index": next_index,
                "recovery_last_sent_at": now.isoformat(),
                "recovery_last_template": template["name"],
                "recovery_delivered": delivered,
                "recovery_status": "sent" if delivered else "send_failed",
                "recovery_last_error": error,
                "recovery_sent": True,
                "recovery_sent_at": item.get("recovery_sent_at") or now.isoformat(),
            }
            if next_index >= len(steps):
                update["recovery_status"] = "completed" if delivered else "send_failed"
            await db.abandoned_orders.update_one({"_id": item["_id"]}, {"$set": update})
            await log_action(str(mid), "abandoned_recovery_processed", actor="system", resource_id=str(item["_id"]), detail={"delivered": delivered, "step": next_index})
            processed += 1
    return {"processed": processed, "skipped": skipped, "run_id": run_id}

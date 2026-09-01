"""Platform cron endpoints. Must ack 2xx immediately; background the real work."""
import os
import hmac
import asyncio
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from database import db
from services.whatsapp_service import whatsapp_service, WhatsAppNotConfigured
from audit import log_action

router = APIRouter(prefix="/api/cron", tags=["cron"])


def _authorize(request: Request):
    secret = os.environ.get("WEBHOOK_CRON_SECRET", "")
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _run_abandoned_recovery(run_id: str):
    # idempotency: skip if this run already processed
    if await db.cron_runs.find_one({"run_id": run_id}):
        return
    await db.cron_runs.insert_one({"run_id": run_id, "job": "abandoned-recovery",
                                   "at": datetime.now(timezone.utc).isoformat()})
    now = datetime.now(timezone.utc)
    merchants = await db.ai_settings.find({"abandoned_recovery": True}).to_list(1000)
    for s in merchants:
        mid = s["merchant_id"]
        delay = int(s.get("abandoned_delay_minutes", 60))
        cutoff = (now - timedelta(minutes=delay)).isoformat()
        pending = await db.abandoned_orders.find({
            "merchant_id": mid, "status": "abandoned",
            "recovery_sent": {"$ne": True},
            "last_interaction": {"$lte": cutoff},
        }).to_list(500)
        if not pending:
            continue
        conn = await db.whatsapp_connections.find_one({"merchant_id": mid})
        tpl = await db.whatsapp_templates.find_one({"merchant_id": mid, "use_case": "abandoned_order"})
        for a in pending:
            sent = False
            try:
                if conn and tpl and a.get("customer_phone"):
                    await whatsapp_service.send_template(
                        conn, a["customer_phone"], tpl["name"], tpl.get("language", "en"))
                    sent = True
            except WhatsAppNotConfigured:
                sent = False
            except Exception:
                sent = False
            await db.abandoned_orders.update_one(
                {"_id": a["_id"]},
                {"$set": {"recovery_sent": True, "recovery_sent_at": now.isoformat(),
                          "recovery_delivered": sent}})
            await log_action(str(mid), "abandoned_recovery_processed", actor="system",
                             resource_id=str(a["_id"]), detail={"delivered": sent})


@router.post("/abandoned-recovery")
async def abandoned_recovery(request: Request, background: BackgroundTasks):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    _authorize(request)
    try:
        envelope = await request.json()
    except Exception:
        envelope = {}
    run_id = request.headers.get("X-Webhook-Id") or envelope.get("run_id") or datetime.now(timezone.utc).isoformat()
    background.add_task(_run_abandoned_recovery, run_id)
    return {"accepted": True, "run_id": run_id}

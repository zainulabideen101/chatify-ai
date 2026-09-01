"""Platform cron endpoints. Must ack 2xx immediately; background the real work."""
import os
import hmac
import asyncio
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from database import db
from services.whatsapp_service import whatsapp_service, WhatsAppNotConfigured
from services.recovery import process_recovery_run

router = APIRouter(prefix="/api/cron", tags=["cron"])


def _authorize(request: Request):
    secret = os.environ.get("WEBHOOK_CRON_SECRET", "")
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _run_abandoned_recovery(run_id: str):
    return await process_recovery_run(run_id)


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

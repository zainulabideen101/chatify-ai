"""Audit logging helper. Every important AI/human/system action is recorded."""
from datetime import datetime, timezone
from database import db


async def log_action(merchant_id: str, action: str, actor: str = "system",
                     detail: dict = None, conversation_id: str = None,
                     resource_id: str = None):
    await db.audit_logs.insert_one({
        "merchant_id": merchant_id,
        "action": action,
        "actor": actor,               # system | ai | human | merchant
        "detail": detail or {},
        "conversation_id": conversation_id,
        "resource_id": resource_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

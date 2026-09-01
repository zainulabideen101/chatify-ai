"""Plan catalog and daily unique-customer chat entitlements.

Billing activation is intentionally provider-backed: paid plans are displayed and
tracked here, but are not activated until a real payment adapter is configured.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from database import db, serialize
from models import iso_now

PKT = ZoneInfo("Asia/Karachi")

PLAN_CATALOG = {
    "free": {
        "id": "free",
        "name": "Free",
        "price_pkr": 0,
        "daily_unique_customer_limit": 15,
        "description": "Try AI WhatsApp selling with the essentials.",
    },
    "starter": {
        "id": "starter",
        "name": "Starter",
        "price_pkr": 2500,
        "daily_unique_customer_limit": 100,
        "description": "For growing stores starting conversations every day.",
    },
    "growth": {
        "id": "growth",
        "name": "Growth",
        "price_pkr": 4000,
        "daily_unique_customer_limit": 250,
        "description": "More room for busy Pakistani ecommerce sellers.",
    },
    "unlimited": {
        "id": "unlimited",
        "name": "Unlimited",
        "price_pkr": 10000,
        "daily_unique_customer_limit": None,
        "description": "Unlimited new customer chats for high-volume stores.",
    },
}


def day_key() -> str:
    """Return the merchant usage date in Pakistan Standard Time."""
    return datetime.now(PKT).date().isoformat()


def _merchant_oid(merchant_id: str):
    return ObjectId(merchant_id) if isinstance(merchant_id, str) else merchant_id


def payment_methods() -> list[dict]:
    return [
        {
            "id": "jazzcash",
            "name": "JazzCash",
            "configured": all(os.environ.get(k) for k in ("JAZZCASH_MERCHANT_ID", "JAZZCASH_PASSWORD", "JAZZCASH_HASH_KEY")),
        },
        {
            "id": "easypaisa",
            "name": "Easypaisa",
            "configured": all(os.environ.get(k) for k in ("EASYPAISA_STORE_ID", "EASYPAISA_USERNAME", "EASYPAISA_PASSWORD")),
        },
        {
            "id": "bank_transfer",
            "name": "Bank transfer",
            "configured": bool(os.environ.get("BANK_IBAN")),
        },
    ]


async def ensure_subscription(merchant_id: str | ObjectId) -> dict:
    oid = _merchant_oid(merchant_id)
    now = iso_now()
    await db.billing_subscriptions.update_one(
        {"merchant_id": oid},
        {
            "$setOnInsert": {
                "merchant_id": oid,
                "plan_id": "free",
                "status": "active",
                "billing_status": "not_configured",
                "started_at": now,
                "created_at": now,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
    )
    return await db.billing_subscriptions.find_one({"merchant_id": oid})


async def ensure_all_subscriptions() -> None:
    async for merchant in db.merchants.find({}, {"_id": 1}):
        await ensure_subscription(merchant["_id"])


async def get_usage_summary(merchant_id: str) -> dict:
    subscription = await ensure_subscription(merchant_id)
    plan = PLAN_CATALOG.get(subscription.get("plan_id"), PLAN_CATALOG["free"])
    today = day_key()
    used = await db.daily_chat_usage.count_documents({"merchant_id": _merchant_oid(merchant_id), "day": today})
    limit = plan["daily_unique_customer_limit"]
    return {
        "day": today,
        "used": used,
        "limit": limit,
        "remaining": None if limit is None else max(limit - used, 0),
        "ai_available_for_new_chats": limit is None or used < limit,
        "plan": plan,
        "subscription": {
            "status": subscription.get("status", "active"),
            "billing_status": subscription.get("billing_status", "not_configured"),
            "started_at": subscription.get("started_at"),
            "current_period_end": subscription.get("current_period_end"),
        },
        "payment_methods": payment_methods(),
    }


async def record_unique_chat(merchant_id: str | ObjectId, customer_phone: str) -> dict:
    """Count a customer once per Pakistan day and decide whether AI may reply.

    The marker is tenant-scoped and unique. A chat that arrives after the plan
    limit is still stored and remains available to a human seller, but receives
    no automatic AI response.
    """
    oid = _merchant_oid(merchant_id)
    phone = (customer_phone or "").strip()
    today = day_key()
    existing = await db.daily_chat_usage.find_one({
        "merchant_id": oid, "day": today, "customer_phone": phone,
    })
    if existing:
        summary = await get_usage_summary(str(oid))
        return {
            "is_new_chat": False,
            "ai_allowed": existing.get("ai_allowed", True),
            "used": summary["used"],
            "limit": summary["limit"],
            "day": today,
        }

    subscription = await ensure_subscription(oid)
    plan = PLAN_CATALOG.get(subscription.get("plan_id"), PLAN_CATALOG["free"])
    used_before = await db.daily_chat_usage.count_documents({"merchant_id": oid, "day": today})
    limit = plan["daily_unique_customer_limit"]
    ai_allowed = limit is None or used_before < limit
    marker = {
        "merchant_id": oid,
        "day": today,
        "customer_phone": phone,
        "ai_allowed": ai_allowed,
        "created_at": iso_now(),
    }
    try:
        await db.daily_chat_usage.insert_one(marker)
    except DuplicateKeyError:
        existing = await db.daily_chat_usage.find_one({
            "merchant_id": oid, "day": today, "customer_phone": phone,
        })
        ai_allowed = existing.get("ai_allowed", True) if existing else ai_allowed
    used = await db.daily_chat_usage.count_documents({"merchant_id": oid, "day": today})
    return {"is_new_chat": True, "ai_allowed": ai_allowed, "used": used, "limit": limit, "day": today}


def plans_payload() -> list[dict]:
    return list(PLAN_CATALOG.values())

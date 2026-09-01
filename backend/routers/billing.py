"""Seller billing, pricing, and daily chat entitlement endpoints.

Payment activation remains disabled until real JazzCash, Easypaisa, or bank
transfer merchant credentials are configured; no payment success is fabricated.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deps import get_tenant_id
from services.billing import get_usage_summary, plans_payload, ensure_subscription

router = APIRouter(prefix="/api/billing", tags=["billing"])


class PlanSelection(BaseModel):
    plan_id: Literal["free", "starter", "growth", "unlimited"]


@router.get("/plans")
async def list_plans(tid: str = Depends(get_tenant_id)):
    return {"plans": plans_payload()}


@router.get("/summary")
async def billing_summary(tid: str = Depends(get_tenant_id)):
    return await get_usage_summary(tid)


@router.post("/plan")
async def select_plan(payload: PlanSelection, tid: str = Depends(get_tenant_id)):
    if payload.plan_id != "free":
        raise HTTPException(
            status_code=503,
            detail="Paid plan activation is not configured yet. Choose JazzCash, Easypaisa, or bank transfer after payment setup is completed.",
        )
    subscription = await ensure_subscription(tid)
    from database import db
    from bson import ObjectId
    from models import iso_now
    await db.billing_subscriptions.update_one(
        {"merchant_id": ObjectId(tid)},
        {"$set": {"plan_id": "free", "status": "active", "billing_status": "not_configured", "updated_at": iso_now()}},
    )
    return await get_usage_summary(tid)

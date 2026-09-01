"""Seller controls for abandoned-cart follow-up automation."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from deps import get_tenant_id
from services.recovery import get_recovery_settings, save_recovery_settings

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


class RecoveryStep(BaseModel):
    delay_minutes: int = Field(default=0, ge=0, le=10080)
    template_name: str = Field(min_length=1, max_length=100)
    language: str = Field(default="en", min_length=2, max_length=10)
    discount_percent: float = Field(default=0, ge=0, le=100)
    enabled: bool = True


class RecoverySettingsUpdate(BaseModel):
    enabled: bool = True
    trigger_delay_minutes: int = Field(default=60, ge=1, le=10080)
    steps: list[RecoveryStep] = Field(default_factory=list, max_length=5)
    stop_on_customer_reply: bool = True
    stop_on_order_recovered: bool = True
    stop_when_seller_pauses: bool = True


@router.get("/settings")
async def recovery_settings(tid: str = Depends(get_tenant_id)):
    return await get_recovery_settings(tid)


@router.put("/settings")
async def update_recovery_settings(payload: RecoverySettingsUpdate, tid: str = Depends(get_tenant_id)):
    return await save_recovery_settings(tid, payload.model_dump())

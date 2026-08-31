"""Pydantic models for the multi-tenant data model. All entities are tenant-aware (merchant_id).

These describe the canonical schema. Read/write of collections goes through motor;
API responses are serialized with database.serialize to keep them JSON-safe.
"""
from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


# ---------- Auth / tenancy ----------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str
    store_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ---------- Merchant / settings ----------
class MerchantUpdate(BaseModel):
    store_name: Optional[str] = None
    contact_phone: Optional[str] = None
    currency: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None


class AISettingsUpdate(BaseModel):
    sales_mode: Optional[Literal["SUPPORT_ONLY", "HELPFUL_SALES", "AGGRESSIVE_SALES"]] = None
    cross_selling: Optional[bool] = None
    upselling: Optional[bool] = None
    abandoned_recovery: Optional[bool] = None
    customer_memory: Optional[bool] = None
    media_sending: Optional[bool] = None
    ai_order_creation: Optional[bool] = None
    human_escalation: Optional[bool] = None
    cod_protection: Optional[bool] = None
    max_discount_percent: Optional[float] = Field(default=None, ge=0, le=100)
    abandoned_delay_minutes: Optional[int] = Field(default=None, ge=0, le=10080)


class BusinessRuleCreate(BaseModel):
    instruction: str
    category: Optional[str] = "general"
    active: bool = True


class KnowledgeDocCreate(BaseModel):
    title: str
    category: str            # shipping | returns | exchange | warranty | faq | size_guide | policy | instruction
    content: str


class TemplateCreate(BaseModel):
    name: str
    use_case: str
    language: str = "en"
    category: str = "UTILITY"
    body: str
    variables: list[str] = []
    meta_template_id: Optional[str] = None


# ---------- Integrations ----------
class ShopifyConnectRequest(BaseModel):
    shop_domain: str          # e.g. my-store.myshopify.com


class WhatsAppConnectRequest(BaseModel):
    waba_id: str
    phone_number_id: str
    display_phone_number: str
    access_token: str


# ---------- Media ----------
class MediaCreate(BaseModel):
    filename: str
    media_type: str           # image | video
    url: str
    product_id: Optional[str] = None
    variant_id: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    tags: list[str] = []
    description: Optional[str] = None


# ---------- Inbox ----------
class SendMessageRequest(BaseModel):
    body: str


class DefaultAISettings:
    @staticmethod
    def make() -> dict:
        return {
            "sales_mode": "HELPFUL_SALES",
            "cross_selling": True,
            "upselling": True,
            "abandoned_recovery": True,
            "customer_memory": True,
            "media_sending": True,
            "ai_order_creation": True,
            "human_escalation": True,
            "cod_protection": True,
            "max_discount_percent": 10,
            "abandoned_delay_minutes": 60,
        }

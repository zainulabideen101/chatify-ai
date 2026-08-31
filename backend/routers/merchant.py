"""Merchant profile, AI settings, business rules, knowledge base, templates, audit logs."""
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from database import db, serialize, serialize_list
from deps import get_tenant_id
from models import (MerchantUpdate, AISettingsUpdate, BusinessRuleCreate,
                    KnowledgeDocCreate, TemplateCreate, iso_now)
from audit import log_action

router = APIRouter(prefix="/api", tags=["merchant"])


# ---------- Merchant profile ----------
@router.get("/merchant")
async def get_merchant(tid: str = Depends(get_tenant_id)):
    m = await db.merchants.find_one({"_id": ObjectId(tid)})
    if not m:
        raise HTTPException(404, "Merchant not found")
    return serialize(m)


@router.put("/merchant")
async def update_merchant(payload: MerchantUpdate, tid: str = Depends(get_tenant_id)):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if updates:
        await db.merchants.update_one({"_id": ObjectId(tid)}, {"$set": updates})
    m = await db.merchants.find_one({"_id": ObjectId(tid)})
    return serialize(m)


# ---------- AI settings ----------
@router.get("/ai-settings")
async def get_ai_settings(tid: str = Depends(get_tenant_id)):
    s = await db.ai_settings.find_one({"merchant_id": ObjectId(tid)})
    return serialize(s)


@router.put("/ai-settings")
async def update_ai_settings(payload: AISettingsUpdate, tid: str = Depends(get_tenant_id)):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if updates:
        await db.ai_settings.update_one({"merchant_id": ObjectId(tid)}, {"$set": updates}, upsert=True)
        await log_action(tid, "ai_settings_updated", actor="merchant", detail=updates)
    s = await db.ai_settings.find_one({"merchant_id": ObjectId(tid)})
    return serialize(s)


# ---------- Business rules (Teach your AI) ----------
@router.get("/business-rules")
async def list_rules(tid: str = Depends(get_tenant_id)):
    docs = await db.business_rules.find({"merchant_id": ObjectId(tid)}).sort("created_at", -1).to_list(500)
    return serialize_list(docs)


@router.post("/business-rules")
async def create_rule(payload: BusinessRuleCreate, tid: str = Depends(get_tenant_id)):
    doc = {"merchant_id": ObjectId(tid), "instruction": payload.instruction,
           "category": payload.category, "active": payload.active, "created_at": iso_now()}
    res = await db.business_rules.insert_one(doc)
    await log_action(tid, "business_rule_created", actor="merchant", detail={"instruction": payload.instruction})
    return serialize({**doc, "_id": res.inserted_id})


@router.delete("/business-rules/{rule_id}")
async def delete_rule(rule_id: str, tid: str = Depends(get_tenant_id)):
    await db.business_rules.delete_one({"_id": ObjectId(rule_id), "merchant_id": ObjectId(tid)})
    return {"ok": True}


# ---------- Knowledge base ----------
@router.get("/knowledge-base")
async def list_kb(tid: str = Depends(get_tenant_id)):
    docs = await db.knowledge_documents.find({"merchant_id": ObjectId(tid)}).sort("created_at", -1).to_list(500)
    return serialize_list(docs)


@router.post("/knowledge-base")
async def create_kb(payload: KnowledgeDocCreate, tid: str = Depends(get_tenant_id)):
    doc = {"merchant_id": ObjectId(tid), "title": payload.title, "category": payload.category,
           "content": payload.content, "created_at": iso_now()}
    res = await db.knowledge_documents.insert_one(doc)
    return serialize({**doc, "_id": res.inserted_id})


@router.delete("/knowledge-base/{doc_id}")
async def delete_kb(doc_id: str, tid: str = Depends(get_tenant_id)):
    await db.knowledge_documents.delete_one({"_id": ObjectId(doc_id), "merchant_id": ObjectId(tid)})
    return {"ok": True}


# ---------- WhatsApp templates ----------
@router.get("/templates")
async def list_templates(tid: str = Depends(get_tenant_id)):
    docs = await db.whatsapp_templates.find({"merchant_id": ObjectId(tid)}).sort("created_at", -1).to_list(500)
    return serialize_list(docs)


@router.post("/templates")
async def create_template(payload: TemplateCreate, tid: str = Depends(get_tenant_id)):
    doc = {"merchant_id": ObjectId(tid), "name": payload.name, "use_case": payload.use_case,
           "language": payload.language, "category": payload.category, "body": payload.body,
           "variables": payload.variables, "meta_template_id": payload.meta_template_id,
           "approval_status": "PENDING" if not payload.meta_template_id else "APPROVED",
           "created_at": iso_now()}
    res = await db.whatsapp_templates.insert_one(doc)
    return serialize({**doc, "_id": res.inserted_id})


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, tid: str = Depends(get_tenant_id)):
    await db.whatsapp_templates.delete_one({"_id": ObjectId(template_id), "merchant_id": ObjectId(tid)})
    return {"ok": True}


# ---------- Audit logs ----------
@router.get("/audit-logs")
async def list_audit(tid: str = Depends(get_tenant_id), limit: int = 100):
    docs = await db.audit_logs.find({"merchant_id": tid}).sort("created_at", -1).to_list(limit)
    return serialize_list(docs)

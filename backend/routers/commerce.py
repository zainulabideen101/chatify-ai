"""Catalog (products, media), customers, orders, abandoned orders, COD, address tool."""
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from database import db, serialize, serialize_list
from deps import get_tenant_id
from models import MediaCreate, iso_now
from services.commerce import address_validator, cod_assessor
from audit import log_action

router = APIRouter(prefix="/api", tags=["commerce"])


def _oid(tid): return ObjectId(tid)


# ---------- Products ----------
@router.get("/products")
async def list_products(tid: str = Depends(get_tenant_id), q: str = None, limit: int = 100):
    query = {"merchant_id": _oid(tid)}
    if q:
        query["title"] = {"$regex": q, "$options": "i"}
    docs = await db.products.find(query).limit(limit).to_list(limit)
    return serialize_list(docs)


@router.get("/products/{product_id}")
async def get_product(product_id: str, tid: str = Depends(get_tenant_id)):
    doc = await db.products.find_one({"_id": ObjectId(product_id), "merchant_id": _oid(tid)})
    if not doc:
        raise HTTPException(404, "Product not found")
    variants = await db.product_variants.find(
        {"product_id": ObjectId(product_id), "merchant_id": _oid(tid)}).to_list(200)
    media = await db.product_media.find(
        {"product_id": ObjectId(product_id), "merchant_id": _oid(tid)}).to_list(200)
    return {**serialize(doc), "variants": serialize_list(variants), "media": serialize_list(media)}


@router.get("/product-relationships")
async def list_relationships(tid: str = Depends(get_tenant_id)):
    docs = await db.product_relationships.find({"merchant_id": _oid(tid)}).to_list(500)
    return serialize_list(docs)


# ---------- Media library ----------
@router.get("/media")
async def list_media(tid: str = Depends(get_tenant_id), tag: str = None):
    query = {"merchant_id": _oid(tid)}
    if tag:
        query["tags"] = tag
    docs = await db.product_media.find(query).sort("created_at", -1).to_list(500)
    return serialize_list(docs)


@router.post("/media")
async def create_media(payload: MediaCreate, tid: str = Depends(get_tenant_id)):
    doc = {
        "merchant_id": _oid(tid), "filename": payload.filename, "media_type": payload.media_type,
        "url": payload.url,
        "product_id": ObjectId(payload.product_id) if payload.product_id else None,
        "variant_id": ObjectId(payload.variant_id) if payload.variant_id else None,
        "color": payload.color, "size": payload.size, "tags": payload.tags,
        "description": payload.description, "created_at": iso_now(),
    }
    res = await db.product_media.insert_one(doc)
    await log_action(tid, "media_uploaded", actor="merchant", detail={"filename": payload.filename})
    return serialize({**doc, "_id": res.inserted_id})


@router.delete("/media/{media_id}")
async def delete_media(media_id: str, tid: str = Depends(get_tenant_id)):
    await db.product_media.delete_one({"_id": ObjectId(media_id), "merchant_id": _oid(tid)})
    return {"ok": True}


# ---------- Customers ----------
@router.get("/customers")
async def list_customers(tid: str = Depends(get_tenant_id), limit: int = 100):
    docs = await db.customers.find({"merchant_id": _oid(tid)}).limit(limit).to_list(limit)
    return serialize_list(docs)


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str, tid: str = Depends(get_tenant_id)):
    doc = await db.customers.find_one({"_id": ObjectId(customer_id), "merchant_id": _oid(tid)})
    if not doc:
        raise HTTPException(404, "Customer not found")
    memory = await db.customer_memory.find_one({"customer_id": ObjectId(customer_id)})
    orders = await db.shopify_orders.find(
        {"customer_id": ObjectId(customer_id), "merchant_id": _oid(tid)}).to_list(100)
    cod = cod_assessor.assess({
        "previous_orders": doc.get("total_orders", 0),
        "delivered_orders": doc.get("delivered_orders", 0),
        "cancelled_orders": doc.get("cancelled_orders", 0),
        "refused_orders": doc.get("refused_orders", 0),
        "failed_deliveries": doc.get("failed_deliveries", 0),
    })
    return {**serialize(doc), "memory": serialize(memory),
            "orders": serialize_list(orders), "cod_risk": cod}


# ---------- Orders ----------
@router.get("/orders")
async def list_orders(tid: str = Depends(get_tenant_id), limit: int = 100):
    docs = await db.shopify_orders.find({"merchant_id": _oid(tid)}).sort("created_at", -1).limit(limit).to_list(limit)
    return serialize_list(docs)


@router.get("/orders/{order_id}")
async def get_order(order_id: str, tid: str = Depends(get_tenant_id)):
    doc = await db.shopify_orders.find_one({"_id": ObjectId(order_id), "merchant_id": _oid(tid)})
    if not doc:
        raise HTTPException(404, "Order not found")
    return serialize(doc)


# ---------- Abandoned orders ----------
@router.get("/abandoned-orders")
async def list_abandoned(tid: str = Depends(get_tenant_id)):
    docs = await db.abandoned_orders.find({"merchant_id": _oid(tid)}).sort("last_interaction", -1).to_list(200)
    return serialize_list(docs)


# ---------- Address validation tool (deterministic backend) ----------
@router.post("/tools/validate-address")
async def validate_address(payload: dict, tid: str = Depends(get_tenant_id)):
    return address_validator.validate(payload.get("address", ""), payload.get("city"))


# ---------- COD risk tool ----------
@router.post("/tools/cod-risk")
async def cod_risk(payload: dict, tid: str = Depends(get_tenant_id)):
    return cod_assessor.assess(payload)

"""Dashboard overview + analytics (sales, revenue attribution, cross/upsell, abandoned, COD, customer)."""
from bson import ObjectId
from fastapi import APIRouter, Depends

from database import db, serialize_list
from deps import get_tenant_id

router = APIRouter(prefix="/api", tags=["analytics"])


def _oid(tid): return ObjectId(tid)


async def _stats(tid: str) -> dict:
    m = _oid(tid)
    conv_total = await db.conversations.count_documents({"merchant_id": m})
    orders = await db.shopify_orders.find({"merchant_id": m}).to_list(2000)
    ai_orders = [o for o in orders if o.get("attribution") == "AI"]
    assisted_orders = [o for o in orders if o.get("attribution") in ("AI", "AI_ASSISTED")]
    human_orders = [o for o in orders if o.get("attribution") == "HUMAN"]
    revenue = sum(o.get("total", 0) for o in orders)
    ai_revenue = sum(o.get("total", 0) for o in ai_orders)
    assisted_revenue = sum(o.get("total", 0) for o in assisted_orders)
    human_revenue = sum(o.get("total", 0) for o in human_orders)
    resolved = await db.conversations.count_documents({"merchant_id": m, "ai_resolved": True})
    takeovers = await db.conversations.count_documents({"merchant_id": m, "had_human_takeover": True})
    abandoned = await db.abandoned_orders.find({"merchant_id": m}).to_list(1000)
    recovered = [a for a in abandoned if a.get("status") == "recovered"]
    aov = round(revenue / len(orders)) if orders else 0
    return {
        "conversations": conv_total,
        "ai_resolution_rate": round((resolved / conv_total) * 100) if conv_total else 0,
        "orders_generated": len(orders),
        "ai_orders": len(ai_orders),
        "ai_attributed_revenue": ai_revenue,
        "ai_assisted_revenue": assisted_revenue,
        "human_only_revenue": human_revenue,
        "total_revenue": revenue,
        "average_order_value": aov,
        "conversion_rate": round((len(orders) / conv_total) * 100, 1) if conv_total else 0,
        "human_takeover_rate": round((takeovers / conv_total) * 100, 1) if conv_total else 0,
        "abandoned_count": len(abandoned),
        "abandoned_value": sum(a.get("order_value", 0) for a in abandoned),
        "recovered_count": len(recovered),
        "recovered_revenue": sum(a.get("order_value", 0) for a in recovered),
        "recovery_rate": round((len(recovered) / len(abandoned)) * 100) if abandoned else 0,
    }


@router.get("/dashboard")
async def dashboard(tid: str = Depends(get_tenant_id)):
    m = _oid(tid)
    stats = await _stats(tid)
    recent_conversations = await db.conversations.find({"merchant_id": m}).sort("last_message_at", -1).limit(6).to_list(6)
    recent_orders = await db.shopify_orders.find({"merchant_id": m}).sort("created_at", -1).limit(6).to_list(6)
    attention = await db.conversations.find({"merchant_id": m, "needs_attention": True}).limit(6).to_list(6)
    abandoned = await db.abandoned_orders.find({"merchant_id": m, "status": {"$ne": "recovered"}}).limit(6).to_list(6)
    low_conf = await db.conversations.find({"merchant_id": m, "low_confidence": True}).limit(6).to_list(6)
    top_products = await db.products.find({"merchant_id": m}).sort("sold_count", -1).limit(5).to_list(5)
    return {
        "stats": stats,
        "recent_conversations": serialize_list(recent_conversations),
        "recent_orders": serialize_list(recent_orders),
        "attention_conversations": serialize_list(attention),
        "abandoned_opportunities": serialize_list(abandoned),
        "low_confidence_events": serialize_list(low_conf),
        "top_products": serialize_list(top_products),
    }


@router.get("/analytics/overview")
async def analytics_overview(tid: str = Depends(get_tenant_id)):
    return await _stats(tid)


@router.get("/analytics/revenue-attribution")
async def revenue_attribution(tid: str = Depends(get_tenant_id)):
    s = await _stats(tid)
    return {
        "ai_attributed_revenue": s["ai_attributed_revenue"],
        "ai_assisted_revenue": s["ai_assisted_revenue"],
        "human_only_revenue": s["human_only_revenue"],
        "ai_orders": s["ai_orders"],
        "total_orders": s["orders_generated"],
        "total_revenue": s["total_revenue"],
    }


@router.get("/analytics/cross-upsell")
async def cross_upsell(tid: str = Depends(get_tenant_id)):
    m = _oid(tid)
    events = await db.recommendation_events.find({"merchant_id": m}).to_list(5000)
    def agg(kind):
        subset = [e for e in events if e.get("kind") == kind]
        accepted = [e for e in subset if e.get("accepted")]
        return {
            "recommendations": len(subset),
            "accepted": len(accepted),
            "acceptance_rate": round((len(accepted) / len(subset)) * 100) if subset else 0,
            "revenue": sum(e.get("revenue", 0) for e in accepted),
        }
    return {"cross_sell": agg("cross_sell"), "upsell": agg("upsell")}


@router.get("/analytics/cod")
async def cod_analytics(tid: str = Depends(get_tenant_id)):
    m = _oid(tid)
    orders = await db.shopify_orders.find({"merchant_id": m, "payment_method": "COD"}).to_list(2000)
    def count(status): return len([o for o in orders if o.get("fulfillment_status") == status])
    total = len(orders)
    delivered = count("delivered")
    return {
        "cod_orders": total,
        "delivered": delivered,
        "cancelled": count("cancelled"),
        "refused": count("refused"),
        "success_rate": round((delivered / total) * 100) if total else 0,
    }


@router.get("/analytics/customers")
async def customer_analytics(tid: str = Depends(get_tenant_id)):
    m = _oid(tid)
    customers = await db.customers.find({"merchant_id": m}).to_list(5000)
    returning = [c for c in customers if c.get("total_orders", 0) > 1]
    lang = {}
    for c in customers:
        l = c.get("preferred_language", "unknown")
        lang[l] = lang.get(l, 0) + 1
    return {
        "total_customers": len(customers),
        "returning_customers": len(returning),
        "new_customers": len(customers) - len(returning),
        "repeat_rate": round((len(returning) / len(customers)) * 100) if customers else 0,
        "language_distribution": lang,
    }

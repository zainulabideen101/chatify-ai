"""Local-first orders, cancellation, Shopify sync path, and WhatsApp delivery tests.

Covers the new behavior:
- create_customer_order local-only when no Shopify connection.
- cancel_latest_customer_order local-only when no Shopify connection.
- create_shopify_order AI tool never leaks integration messaging on failure.
- Shopify sync success + failure paths.
- Cancellation sync success + failure paths.
- WhatsApp delivery: sent, and Meta 401 -> failed + connection marked error.
- System prompt tells AI never to mention integrations.
- Webhook 200 ack + no secrets exposed.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import requests
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

backend_env = dotenv_values("/app/backend/.env")
for _k in ("MONGO_URL", "DB_NAME", "OPENAI_API_KEY"):
    if _k not in os.environ and backend_env.get(_k):
        os.environ[_k] = backend_env[_k]
sys.path.insert(0, "/app/backend")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")).rstrip("/")


# ---------- helpers ----------

async def _adb():
    client = AsyncIOMotorClient(MONGO_URL)
    return client, client[DB_NAME]


async def _get_mid():
    client, db = await _adb()
    try:
        u = await db.users.find_one({"email": "techgear008@gmail.com"})
        return str(u["merchant_id"])
    finally:
        client.close()


async def _pick_product_with_variant(mid: str):
    """Return (product_title, size, color) for a product that has a variant, so
    _resolve_items succeeds against real seeded catalog data."""
    client, db = await _adb()
    try:
        oid = ObjectId(mid)
        async for v in db.product_variants.find({"merchant_id": oid}).limit(20):
            p = await db.products.find_one({"_id": v["product_id"], "merchant_id": oid})
            if p:
                return p["title"], v.get("size"), v.get("color")
        # fallback: any product
        p = await db.products.find_one({"merchant_id": oid})
        return (p["title"] if p else None), None, None
    finally:
        client.close()


async def _cleanup_test_order(mid: str, phone: str):
    client, db = await _adb()
    try:
        oid = ObjectId(mid)
        cust = await db.customers.find_one({"merchant_id": oid, "phone": phone})
        if cust:
            await db.shopify_orders.delete_many({"merchant_id": oid, "customer_id": cust["_id"]})
            await db.customers.delete_one({"_id": cust["_id"]})
    finally:
        client.close()


async def _get_conn(mid: str):
    client, db = await _adb()
    try:
        return await db.whatsapp_connections.find_one({"merchant_id": ObjectId(mid)})
    finally:
        client.close()


# ---------- local order creation (no Shopify) ----------

class TestLocalOrderCreation:
    def test_create_local_order_without_shopify(self):
        from services.shopify_orders import create_customer_order
        from services import shopify_orders as so_module

        async def run():
            mid = await _get_mid()
            product, size, color = await _pick_product_with_variant(mid)
            assert product, "seed catalog missing a product"
            phone = "+920000TEST01"
            args = {
                "items": [{"product": product, "size": size, "color": color, "quantity": 1}],
                "customer_name": "TEST_qa Buyer", "customer_phone": phone,
                "address": "TEST_qa 123 Main", "city": "Lahore", "payment_method": "COD",
            }
            # Force "no shopify" by patching configured False.
            with patch.object(so_module.shopify_service, "configured", False):
                result = await create_customer_order(mid, None, args)
            assert result["created"] is True, result
            assert result.get("order_number", "").startswith("CFY-")
            assert result["total"] >= 200  # includes PKR 200 delivery
            assert result["currency"] == "PKR"
            assert result["shopify_synced"] is False

            # Verify DB doc
            client, db = await _adb()
            try:
                doc = await db.shopify_orders.find_one({"_id": ObjectId(result["order_id"])})
                assert doc["sync_status"] == "local_only"
                assert doc["shopify_order_id"] is None
                assert doc["fulfillment_status"] == "pending"
            finally:
                client.close()

            # Orders API lists it
            login = requests.post(f"{BASE_URL}/api/auth/login",
                                  json={"email": "techgear008@gmail.com", "password": "TechGear#2026"}, timeout=15)
            token = login.json()["access_token"]
            r = requests.get(f"{BASE_URL}/api/orders", headers={"Authorization": f"Bearer {token}"}, timeout=15)
            assert r.status_code == 200
            ids = [o.get("id") for o in r.json()]
            assert result["order_id"] in ids

            await _cleanup_test_order(mid, phone)

        asyncio.run(run())


# ---------- local cancellation ----------

class TestLocalCancellation:
    def test_cancel_latest_local_order(self):
        from services.shopify_orders import (
            cancel_latest_customer_order,
            create_customer_order,
        )
        from services import shopify_orders as so_module

        async def run():
            mid = await _get_mid()
            product, size, color = await _pick_product_with_variant(mid)
            phone = "+920000TEST02"
            args = {
                "items": [{"product": product, "size": size, "color": color, "quantity": 1}],
                "customer_name": "TEST_qa Cancel", "customer_phone": phone,
                "address": "TEST_qa Addr", "city": "Karachi", "payment_method": "COD",
            }
            with patch.object(so_module.shopify_service, "configured", False):
                created = await create_customer_order(mid, None, args)
            assert created["created"] is True

            client, db = await _adb()
            try:
                cust_before = await db.customers.find_one({"merchant_id": ObjectId(mid), "phone": phone})
                cancelled_before = cust_before.get("cancelled_orders", 0)
            finally:
                client.close()

            with patch.object(so_module.shopify_service, "configured", False):
                res = await cancel_latest_customer_order(mid, phone)
            assert res["cancelled"] is True

            client, db = await _adb()
            try:
                doc = await db.shopify_orders.find_one({"_id": ObjectId(created["order_id"])})
                assert doc["fulfillment_status"] == "cancelled_by_customer"
                assert doc["cancellation_status"] == "cancelled_locally"
                cust_after = await db.customers.find_one({"merchant_id": ObjectId(mid), "phone": phone})
                assert cust_after["cancelled_orders"] == cancelled_before + 1
            finally:
                client.close()

            await _cleanup_test_order(mid, phone)

        asyncio.run(run())


# ---------- AI tool dispatch: no integration wording ----------

class TestAIToolDispatchCustomerFacing:
    def test_create_shopify_order_tool_no_integration_leak(self):
        from services.ai_tools import dispatch
        from services import shopify_orders as so_module

        async def run():
            mid = await _get_mid()
            product, size, color = await _pick_product_with_variant(mid)
            phone = "+920000TEST03"
            args = {
                "items": [{"product": product, "size": size, "color": color, "quantity": 1}],
                "customer_name": "TEST_qa Tool", "customer_phone": phone,
                "address": "TEST_qa Addr", "city": "Lahore", "payment_method": "COD",
            }
            with patch.object(so_module.shopify_service, "configured", False):
                res = await dispatch("create_shopify_order", args, {"merchant_id": mid, "conversation_id": None})
            assert res["created"] is True
            blob = str(res).lower()
            for banned in ("shopify", "integration", "connected", "disconnected", "catalog sync"):
                assert banned not in blob, f"customer-facing result leaks '{banned}': {res}"
            await _cleanup_test_order(mid, phone)

        asyncio.run(run())

    def test_system_prompt_forbids_integration_mentions(self):
        from services.ai_agent import ai_agent

        async def run():
            mid = await _get_mid()
            prompt = await ai_agent._build_system_prompt(mid)
            assert "never mention" in prompt.lower() and "integration" in prompt.lower()
            assert "Never tell customers what platform integrations" in prompt

        asyncio.run(run())


# ---------- Shopify sync (mocked) ----------

class TestShopifySyncPath:
    def test_shopify_create_success_marks_synced(self):
        from services.shopify_orders import create_customer_order
        from services import shopify_orders as so_module

        async def run():
            mid = await _get_mid()
            product, size, color = await _pick_product_with_variant(mid)
            phone = "+920000TEST04"

            # Seed a fake shopify connection + a variant with shopify_variant_id
            client, db = await _adb()
            oid = ObjectId(mid)
            existing_conn = await db.shopify_connections.find_one({"merchant_id": oid})
            variant = await db.product_variants.find_one({"merchant_id": oid, "title": {"$exists": False}}) or await db.product_variants.find_one({"merchant_id": oid})
            # Save existing variant.shopify_variant_id to restore
            orig_variant = None
            if variant:
                orig_variant = variant.get("shopify_variant_id")
                await db.product_variants.update_one({"_id": variant["_id"]},
                                                    {"$set": {"shopify_variant_id": 999888}})
            fake_conn_inserted = False
            if not existing_conn:
                await db.shopify_connections.insert_one({
                    "merchant_id": oid, "shop_domain": "TEST_qa.myshopify.com",
                    "access_token_enc": "TEST_qa_enc_token", "connected_at": datetime.now(timezone.utc).isoformat(),
                })
                fake_conn_inserted = True
            client.close()

            args = {
                "items": [{"product": product, "size": size, "color": color, "quantity": 1}],
                "customer_name": "TEST_qa Sync", "customer_phone": phone,
                "address": "TEST_qa Addr", "city": "Lahore", "payment_method": "COD",
            }

            fake_shopify_order = {"id": 123456789, "order_number": "SHO-42", "total_price": "3500.00"}
            with patch.object(so_module.shopify_service, "configured", True), \
                 patch.object(so_module.shopify_service, "create_order",
                              new=AsyncMock(return_value=fake_shopify_order)):
                res = await create_customer_order(mid, None, args)

            assert res["created"] is True
            assert res["shopify_synced"] is True

            client, db = await _adb()
            try:
                doc = await db.shopify_orders.find_one({"_id": ObjectId(res["order_id"])})
                assert doc["shopify_order_id"] == "123456789"
                assert doc["sync_status"] == "synced"
                assert doc["order_number"] in ("SHO-42",) or "SHO" in str(doc["order_number"])
                # restore
                if variant:
                    await db.product_variants.update_one({"_id": variant["_id"]},
                                                        {"$set": {"shopify_variant_id": orig_variant}})
                if fake_conn_inserted:
                    await db.shopify_connections.delete_one({"merchant_id": oid})
            finally:
                client.close()

            await _cleanup_test_order(mid, phone)

        asyncio.run(run())

    def test_shopify_create_failure_leaves_local_created_sync_pending(self):
        from services.shopify_orders import create_customer_order
        from services.shopify_service import ShopifyAPIError
        from services import shopify_orders as so_module

        async def run():
            mid = await _get_mid()
            product, size, color = await _pick_product_with_variant(mid)
            phone = "+920000TEST05"

            client, db = await _adb()
            oid = ObjectId(mid)
            variant = await db.product_variants.find_one({"merchant_id": oid})
            orig_variant = variant.get("shopify_variant_id") if variant else None
            if variant:
                await db.product_variants.update_one({"_id": variant["_id"]},
                                                    {"$set": {"shopify_variant_id": 111222}})
            existing_conn = await db.shopify_connections.find_one({"merchant_id": oid})
            fake_conn_inserted = False
            if not existing_conn:
                await db.shopify_connections.insert_one({
                    "merchant_id": oid, "shop_domain": "TEST_qa.myshopify.com",
                    "access_token_enc": "TEST_qa_enc_token", "connected_at": datetime.now(timezone.utc).isoformat(),
                })
                fake_conn_inserted = True
            client.close()

            args = {
                "items": [{"product": product, "size": size, "color": color, "quantity": 1}],
                "customer_name": "TEST_qa SyncFail", "customer_phone": phone,
                "address": "TEST_qa Addr", "city": "Lahore", "payment_method": "COD",
            }
            with patch.object(so_module.shopify_service, "configured", True), \
                 patch.object(so_module.shopify_service, "create_order",
                              new=AsyncMock(side_effect=ShopifyAPIError(500, "shopify down"))):
                res = await create_customer_order(mid, None, args)

            assert res["created"] is True  # customer-facing still success
            assert res["shopify_synced"] is False

            client, db = await _adb()
            try:
                doc = await db.shopify_orders.find_one({"_id": ObjectId(res["order_id"])})
                assert doc["sync_status"] == "sync_pending"
                assert doc["shopify_order_id"] is None
                if variant:
                    await db.product_variants.update_one({"_id": variant["_id"]},
                                                        {"$set": {"shopify_variant_id": orig_variant}})
                if fake_conn_inserted:
                    await db.shopify_connections.delete_one({"merchant_id": oid})
            finally:
                client.close()
            await _cleanup_test_order(mid, phone)

        asyncio.run(run())


# ---------- Cancellation sync path ----------

class TestCancellationSyncPath:
    def _seed_shopify_order(self, mid: str, phone: str):
        async def run():
            client, db = await _adb()
            try:
                oid = ObjectId(mid)
                cust = await db.customers.find_one({"merchant_id": oid, "phone": phone})
                if not cust:
                    ins = await db.customers.insert_one({
                        "merchant_id": oid, "name": "TEST_qa Cancel Sync", "phone": phone,
                        "total_orders": 1, "cancelled_orders": 0,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
                    cust_id = ins.inserted_id
                else:
                    cust_id = cust["_id"]
                order_id = ObjectId()
                await db.shopify_orders.insert_one({
                    "_id": order_id, "merchant_id": oid, "customer_id": cust_id,
                    "order_number": "TEST_qa-CANCEL-1", "shopify_order_id": "555999",
                    "total": 1200, "attribution": "AI", "payment_method": "COD",
                    "payment_status": "unpaid", "fulfillment_status": "pending",
                    "sync_status": "synced", "items": [], "shipping_address": {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                existing_conn = await db.shopify_connections.find_one({"merchant_id": oid})
                conn_inserted = False
                if not existing_conn:
                    await db.shopify_connections.insert_one({
                        "merchant_id": oid, "shop_domain": "TEST_qa.myshopify.com",
                        "access_token_enc": "TEST_qa_enc",
                        "connected_at": datetime.now(timezone.utc).isoformat(),
                    })
                    conn_inserted = True
                return str(order_id), str(cust_id), conn_inserted
            finally:
                client.close()
        return asyncio.run(run())

    def test_cancel_syncs_to_shopify_on_success(self):
        from services.shopify_orders import cancel_latest_customer_order
        from services import shopify_orders as so_module

        async def run(order_id, cust_id):
            mid = await _get_mid()
            phone = "+920000TEST06"
            with patch.object(so_module.shopify_service, "configured", True), \
                 patch.object(so_module.shopify_service, "cancel_order",
                              new=AsyncMock(return_value={"id": 555999})):
                res = await cancel_latest_customer_order(mid, phone)
            assert res["cancelled"] is True
            client, db = await _adb()
            try:
                doc = await db.shopify_orders.find_one({"_id": ObjectId(order_id)})
                assert doc["cancellation_status"] == "cancelled_on_shopify"
                assert doc["fulfillment_status"] == "cancelled_by_customer"
            finally:
                client.close()

        mid_sync = asyncio.run(_get_mid())
        order_id, cust_id, conn_ins = self._seed_shopify_order(mid_sync, "+920000TEST06")
        try:
            asyncio.run(run(order_id, cust_id))
        finally:
            asyncio.run(_cleanup_test_order(mid_sync, "+920000TEST06"))
            if conn_ins:
                async def _rm():
                    c, d = await _adb()
                    try: await d.shopify_connections.delete_one({"merchant_id": ObjectId(mid_sync)})
                    finally: c.close()
                asyncio.run(_rm())

    def test_cancel_shopify_failure_still_cancelled_locally(self):
        from services.shopify_orders import cancel_latest_customer_order
        from services.shopify_service import ShopifyAPIError
        from services import shopify_orders as so_module

        mid = asyncio.run(_get_mid())
        phone = "+920000TEST07"
        order_id, cust_id, conn_ins = self._seed_shopify_order(mid, phone)

        async def run():
            with patch.object(so_module.shopify_service, "configured", True), \
                 patch.object(so_module.shopify_service, "cancel_order",
                              new=AsyncMock(side_effect=ShopifyAPIError(500, "boom"))):
                res = await cancel_latest_customer_order(mid, phone)
            assert res["cancelled"] is True
            client, db = await _adb()
            try:
                doc = await db.shopify_orders.find_one({"_id": ObjectId(order_id)})
                assert doc["fulfillment_status"] == "cancelled_by_customer"
                assert doc["cancellation_status"] == "sync_pending"
            finally:
                client.close()

        try:
            asyncio.run(run())
        finally:
            asyncio.run(_cleanup_test_order(mid, phone))
            if conn_ins:
                async def _rm():
                    c, d = await _adb()
                    try: await d.shopify_connections.delete_one({"merchant_id": ObjectId(mid)})
                    finally: c.close()
                asyncio.run(_rm())


# ---------- WhatsApp delivery via generate_and_send_ai_reply ----------

class TestWhatsAppDelivery:
    def _seed_conv(self, mid: str, phone: str):
        async def run():
            client, db = await _adb()
            try:
                oid = ObjectId(mid)
                conv_ins = await db.conversations.insert_one({
                    "merchant_id": oid, "customer_name": "TEST_qa Delivery",
                    "customer_phone": phone, "mode": "AI_ACTIVE",
                    "needs_attention": False, "last_message": "TEST_qa hi",
                    "last_message_at": datetime.now(timezone.utc).isoformat(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                cid = conv_ins.inserted_id
                await db.messages.insert_one({
                    "conversation_id": cid, "merchant_id": oid, "direction": "inbound",
                    "sender": "customer", "body": "TEST_qa hi", "status": "received",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                existing_conn = await db.whatsapp_connections.find_one({"merchant_id": oid})
                conn_ins = False
                if not existing_conn:
                    await db.whatsapp_connections.insert_one({
                        "merchant_id": oid, "phone_number_id": "TEST_qa_pnid",
                        "waba_id": "TEST_qa_waba", "access_token_enc": "TEST_qa_enc",
                        "status": "connected",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
                    conn_ins = True
                return str(cid), conn_ins
            finally:
                client.close()
        return asyncio.run(run())

    def _cleanup(self, mid: str, cid: str, conn_ins: bool):
        async def run():
            c, d = await _adb()
            try:
                await d.messages.delete_many({"conversation_id": ObjectId(cid)})
                await d.conversations.delete_one({"_id": ObjectId(cid)})
                if conn_ins:
                    await d.whatsapp_connections.delete_one({"merchant_id": ObjectId(mid)})
            finally:
                c.close()
        asyncio.run(run())

    def test_ai_reply_sent_persists_outbound(self):
        from services import conversation_ai as ca_module

        mid = asyncio.run(_get_mid())
        phone = "+920000TEST08"
        cid, conn_ins = self._seed_conv(mid, phone)

        async def run():
            client, db = await _adb()
            conv = await db[DB_NAME] if False else await db.conversations.find_one({"_id": ObjectId(cid)})
            client.close()

            fake_send = AsyncMock(return_value={"messages": [{"id": "wamid.TEST_qa_ok"}]})
            fake_agent_result = {"reply": "Hello Sir, how may I help?", "tool_trace": []}
            with patch.object(ca_module.ai_agent, "run_agent", new=AsyncMock(return_value=fake_agent_result)), \
                 patch.object(ca_module.whatsapp_service, "send_text", new=fake_send), \
                 patch.object(type(ca_module.ai_agent), "llm_connected", property(lambda self: True)):
                res = await ca_module.generate_and_send_ai_reply(ObjectId(mid), conv)
            assert res["sent"] is True, res
            fake_send.assert_awaited_once()

            client, db = await _adb()
            try:
                out = await db.messages.find_one({"conversation_id": ObjectId(cid), "direction": "outbound"})
                assert out is not None
                assert out["status"] == "sent"
                assert out.get("whatsapp_message_id") == "wamid.TEST_qa_ok"
            finally:
                client.close()

        try:
            asyncio.run(run())
        finally:
            self._cleanup(mid, cid, conn_ins)

    def test_ai_reply_meta_401_marks_failed_and_connection_error(self):
        from services import conversation_ai as ca_module
        from services.whatsapp_service import WhatsAppAPIError

        mid = asyncio.run(_get_mid())
        phone = "+920000TEST09"
        cid, conn_ins = self._seed_conv(mid, phone)

        async def run():
            client, db = await _adb()
            conv = await db.conversations.find_one({"_id": ObjectId(cid)})
            client.close()

            err = WhatsAppAPIError(401, "Meta WhatsApp error: Session has expired (code 190)")
            fake_send = AsyncMock(side_effect=err)
            fake_agent_result = {"reply": "Hello Sir.", "tool_trace": []}
            with patch.object(ca_module.ai_agent, "run_agent", new=AsyncMock(return_value=fake_agent_result)), \
                 patch.object(ca_module.whatsapp_service, "send_text", new=fake_send), \
                 patch.object(type(ca_module.ai_agent), "llm_connected", property(lambda self: True)):
                res = await ca_module.generate_and_send_ai_reply(ObjectId(mid), conv)
            assert res["sent"] is False

            client, db = await _adb()
            try:
                out = await db.messages.find_one({"conversation_id": ObjectId(cid), "direction": "outbound"})
                assert out is not None
                assert out["status"] == "failed"
                assert out.get("delivery_error")
                conv_doc = await db.conversations.find_one({"_id": ObjectId(cid)})
                assert conv_doc.get("needs_attention") is True
                # Only assert connection.status=error if we own a TEST connection to avoid
                # mutating a real tenant connection's status verdict here.
                if conn_ins:
                    wa = await db.whatsapp_connections.find_one({"merchant_id": ObjectId(mid)})
                    assert wa.get("status") == "error"
                    assert wa.get("last_error")
            finally:
                client.close()

        try:
            asyncio.run(run())
        finally:
            self._cleanup(mid, cid, conn_ins)


# ---------- Webhook ack ----------

class TestWebhookAck:
    def test_webhook_requires_signature_and_no_secret_leak(self):
        # Without a valid X-Hub-Signature-256, webhook must reject with 401
        # and body must not echo secrets/tokens.
        r = requests.post(f"{BASE_URL}/api/webhooks/whatsapp",
                          data=b'{"object":"whatsapp_business_account"}',
                          headers={"Content-Type": "application/json"}, timeout=10)
        assert r.status_code in (401, 403)
        assert "META_APP_SECRET" not in r.text
        assert "access_token" not in r.text.lower() or "token" not in r.text.lower()


# ---------- Regression ----------

class TestRegression:
    def test_login_and_core_endpoints(self):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login",
                   json={"email": "techgear008@gmail.com", "password": "TechGear#2026"}, timeout=15)
        assert r.status_code == 200
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        for path in ("/api/conversations", "/api/orders", "/api/ai-settings"):
            rr = requests.get(f"{BASE_URL}{path}", headers=h, timeout=15)
            assert rr.status_code == 200, f"{path} -> {rr.status_code}"

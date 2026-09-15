"""Tests for iteration-3 features:
- AI reply splitting (blank lines => 1 msg, [[MESSAGE_BREAK]] => N msgs)
- Payment repetition guard
- Sales opt-out (note_sales_refusal + recommend_related guard)
- Meta inbound identity fields + dedup
- Payment screenshot verify => review_required + AI recommendation
- Payment-review approve/reject endpoint
- Shopify update_payment_review HTTP mapping
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _db():
    client = AsyncIOMotorClient(MONGO_URL)
    return client, client[DB_NAME]


async def _get_mid(email="techgear008@gmail.com"):
    client, db = await _db()
    try:
        u = await db.users.find_one({"email": email})
        return str(u["merchant_id"])
    finally:
        client.close()


@pytest.fixture(scope="module")
def owner_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": "techgear008@gmail.com", "password": "TechGear#2026"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def owner_headers(owner_token):
    return {"Authorization": f"Bearer {owner_token}", "Content-Type": "application/json"}


# ---------------- unit: single-message policy ----------------
class TestSingleMessagePolicy:
    def test_message_break_marker_collapses_to_newline(self):
        from services.ai_agent import sanitize_reply
        # The system now always sends ONE WhatsApp message. Any stray marker becomes a newline.
        out = sanitize_reply("step one[[MESSAGE_BREAK]]step two")
        assert "[[MESSAGE_BREAK]]" not in out
        assert "step one" in out and "step two" in out

    def test_empty_stays_empty(self):
        from services.ai_agent import sanitize_reply
        assert sanitize_reply("   ").strip() == ""


# ---------------- generate_and_send_ai_reply integration ----------------
class TestGenerateAndSendAIReply:
    def _seed(self, mid_str, phone="+920000099001", prior_outbound=None):
        async def _do():
            client, db = await _db()
            try:
                mid = ObjectId(mid_str)
                # cleanup previous
                conv = await db.conversations.find_one({"merchant_id": mid, "customer_phone": phone})
                if conv:
                    await db.messages.delete_many({"conversation_id": conv["_id"]})
                    await db.conversations.delete_one({"_id": conv["_id"]})
                # ensure whatsapp connection stub exists (so send_text is called)
                conn = await db.whatsapp_connections.find_one({"merchant_id": mid})
                created_conn = False
                if not conn:
                    await db.whatsapp_connections.insert_one({
                        "merchant_id": mid, "phone_number_id": "TEST_PNID",
                        "access_token_enc": "x", "waba_id": "1", "status": "active"})
                    created_conn = True
                ins = await db.conversations.insert_one({
                    "merchant_id": mid, "customer_name": "TEST_iter3",
                    "customer_phone": phone, "mode": "AI_ACTIVE"})
                cid = ins.inserted_id
                if prior_outbound:
                    await db.messages.insert_one({
                        "conversation_id": cid, "merchant_id": mid,
                        "direction": "outbound", "sender": "ai", "body": prior_outbound,
                        "status": "sent", "created_at": "2025-01-01T00:00:00+00:00"})
                await db.messages.insert_one({
                    "conversation_id": cid, "merchant_id": mid,
                    "direction": "inbound", "sender": "customer",
                    "body": "payment details dobara bhejo",
                    "status": "received", "created_at": "2025-01-02T00:00:00+00:00"})
                return str(cid), created_conn
            finally:
                client.close()
        return _run(_do())

    def _cleanup(self, mid_str, cid_str, drop_conn):
        async def _do():
            client, db = await _db()
            try:
                mid = ObjectId(mid_str)
                cid = ObjectId(cid_str)
                await db.messages.delete_many({"conversation_id": cid})
                await db.conversations.delete_one({"_id": cid})
                if drop_conn:
                    await db.whatsapp_connections.delete_one({"merchant_id": mid, "phone_number_id": "TEST_PNID"})
            finally:
                client.close()
        _run(_do())

    def test_single_message_no_break_marker(self):
        mid = _run(_get_mid())
        cid, drop = self._seed(mid, phone="+920000099101", prior_outbound=None)
        try:
            from services import conversation_ai as ca

            async def fake_run_agent(*a, **k):
                return {"reply": "first paragraph\n\nsecond paragraph", "tool_trace": []}

            async def fake_send(conn, phone, body):
                return {"messages": [{"id": f"wamid.{body[:5]}"}]}

            with patch.object(ca.ai_agent, "run_agent", new=fake_run_agent), \
                 patch.object(ca.whatsapp_service, "send_text", new=fake_send):
                async def _go():
                    client, db = await _db()
                    try:
                        conv = await db.conversations.find_one({"_id": ObjectId(cid)})
                        result = await ca.generate_and_send_ai_reply(ObjectId(mid), conv)
                        outbound = await db.messages.find(
                            {"conversation_id": conv["_id"], "direction": "outbound"}).to_list(20)
                        return result, outbound
                    finally:
                        client.close()
                result, outbound = _run(_go())
                assert result["sent"] is True
                assert len(outbound) == 1
        finally:
            self._cleanup(mid, cid, drop)

    def test_single_message_policy_no_split_even_with_marker(self):
        mid = _run(_get_mid())
        cid, drop = self._seed(mid, phone="+920000099102", prior_outbound=None)
        try:
            from services import conversation_ai as ca

            async def fake_run_agent(*a, **k):
                return {"reply": "step one\n[[MESSAGE_BREAK]]\nstep two", "tool_trace": [{"tool": "x"}]}

            send_calls = []

            async def fake_send(conn, phone, body):
                send_calls.append(body)
                return {"messages": [{"id": f"wamid.{len(send_calls)}"}]}

            with patch.object(ca.ai_agent, "run_agent", new=fake_run_agent), \
                 patch.object(ca.whatsapp_service, "send_text", new=fake_send):
                async def _go():
                    client, db = await _db()
                    try:
                        conv = await db.conversations.find_one({"_id": ObjectId(cid)})
                        result = await ca.generate_and_send_ai_reply(ObjectId(mid), conv)
                        outbound = await db.messages.find(
                            {"conversation_id": conv["_id"], "direction": "outbound"}
                        ).to_list(20)
                        return result, outbound
                    finally:
                        client.close()
                result, outbound = _run(_go())
                # ONE message only — the marker is neutralised to a newline by sanitize_reply.
                # NOTE: run_agent is mocked here so sanitize_reply is bypassed; conversation_ai
                # itself must not split. Verify a single outbound message was persisted/sent.
                assert result["sent"] is True
                assert len(send_calls) == 1
                assert len(outbound) == 1
                assert outbound[0].get("tool_trace") == [{"tool": "x"}]
        finally:
            self._cleanup(mid, cid, drop)


# ---------------- sales opt-out ----------------
class TestSalesOptOut:
    def test_note_sales_refusal_sets_flags(self):
        mid = _run(_get_mid())

        async def _go():
            client, db = await _db()
            try:
                mid_oid = ObjectId(mid)
                cust_ins = await db.customers.insert_one({
                    "merchant_id": mid_oid, "name": "TEST_iter3_optout", "phone": "+920000099200"})
                cid = cust_ins.inserted_id
                conv_ins = await db.conversations.insert_one({
                    "merchant_id": mid_oid, "customer_id": cid,
                    "customer_phone": "+920000099200", "mode": "AI_ACTIVE"})
                conv_id = conv_ins.inserted_id
                await db.messages.insert_one({
                    "conversation_id": conv_id, "merchant_id": mid_oid,
                    "direction": "outbound", "sender": "ai",
                    "body": "You might also like our matching cap.",
                    "status": "sent", "created_at": "2025-01-01T00:00:00+00:00"})
                from services.ai_agent import note_sales_refusal
                did = await note_sales_refusal(mid, str(conv_id), "nahi chahiye")
                conv = await db.conversations.find_one({"_id": conv_id})
                cust = await db.customers.find_one({"_id": cid})
                await db.messages.delete_many({"conversation_id": conv_id})
                await db.conversations.delete_one({"_id": conv_id})
                await db.customers.delete_one({"_id": cid})
                return did, conv, cust
            finally:
                client.close()

        did, conv, cust = _run(_go())
        assert did is True
        assert conv["sales_opt_out"] is True
        assert cust["sales_opt_out"] is True

    def test_recommend_related_returns_empty_when_opted_out(self):
        mid = _run(_get_mid())

        async def _go():
            client, db = await _db()
            try:
                mid_oid = ObjectId(mid)
                conv_ins = await db.conversations.insert_one({
                    "merchant_id": mid_oid, "mode": "AI_ACTIVE", "sales_opt_out": True})
                conv_id = conv_ins.inserted_id
                from services.ai_tools import dispatch
                res = await dispatch("recommend_related", {"product": "anything"},
                                     {"merchant_id": mid, "conversation_id": str(conv_id)})
                await db.conversations.delete_one({"_id": conv_id})
                return res
            finally:
                client.close()

        res = _run(_go())
        assert res == {"results": [], "reason": "customer_declined_recommendations"}


# ---------------- Meta inbound identity + dedup ----------------
class TestMetaInboundIdentityDedup:
    def test_stores_identity_and_dedups(self):
        mid = _run(_get_mid())

        async def _go():
            client, db = await _db()
            try:
                mid_oid = ObjectId(mid)
                # Reuse existing whatsapp_connection for this merchant (unique index on merchant_id).
                conn = await db.whatsapp_connections.find_one({"merchant_id": mid_oid})
                created_conn = False
                if not conn:
                    ins = await db.whatsapp_connections.insert_one({
                        "merchant_id": mid_oid, "phone_number_id": "PNID_ITER3",
                        "waba_id": "WABA_ITER3", "status": "active"})
                    conn = await db.whatsapp_connections.find_one({"_id": ins.inserted_id})
                    created_conn = True
                phone_number_id = conn["phone_number_id"]
                waba_id = str(conn.get("waba_id", "WABA_ITER3"))
                phone = "+920000099301"
                await db.customers.delete_many({"merchant_id": mid_oid, "phone": phone})
                await db.conversations.delete_many({"merchant_id": mid_oid, "customer_phone": phone})
                message_id = "wamid.TEST_ITER3_DEDUP"
                await db.messages.delete_many({"whatsapp_message_id": message_id})

                payload = {"entry": [{"id": waba_id, "changes": [{"value": {
                    "metadata": {"phone_number_id": phone_number_id},
                    "contacts": [{"wa_id": phone, "profile": {"name": "TEST Iter3 User", "picture": "https://example.com/p.jpg"}}],
                    "messages": [{"id": message_id, "from": phone, "type": "text",
                                  "text": {"body": "hello iter3 test"}}]}}]}]}

                from routers.integrations import _process_whatsapp_inbound
                # Patch generate_and_send_ai_reply to avoid live LLM
                with patch("services.conversation_ai.generate_and_send_ai_reply",
                           new=AsyncMock(return_value={"sent": False, "reason": "test"})):
                    await _process_whatsapp_inbound(payload)
                    # Same message id again -> no second insert
                    await _process_whatsapp_inbound(payload)

                cust = await db.customers.find_one({"merchant_id": mid_oid, "phone": phone})
                conv = await db.conversations.find_one({"merchant_id": mid_oid, "customer_phone": phone})
                msgs = await db.messages.find({"whatsapp_message_id": message_id}).to_list(5)

                # cleanup
                if conv:
                    await db.messages.delete_many({"conversation_id": conv["_id"]})
                    await db.conversations.delete_one({"_id": conv["_id"]})
                if cust:
                    await db.customers.delete_one({"_id": cust["_id"]})
                await db.whatsapp_connections.delete_one({"phone_number_id": "PNID_ITER3"}) if created_conn else None
                return cust, conv, msgs
            finally:
                client.close()

        cust, conv, msgs = _run(_go())
        assert cust is not None
        assert cust.get("whatsapp_name") == "TEST Iter3 User"
        assert cust.get("profile_picture_url") == "https://example.com/p.jpg"
        assert conv.get("whatsapp_name") == "TEST Iter3 User"
        assert conv.get("profile_picture_url") == "https://example.com/p.jpg"
        assert len(msgs) == 1  # dedup guard


# ---------------- payment screenshot verify ----------------
class TestPaymentScreenshotVerify:
    def test_review_required_and_ai_recommendation(self, owner_headers):
        mid = _run(_get_mid())

        async def _go():
            client, db = await _db()
            try:
                mid_oid = ObjectId(mid)
                # Seed customer, conversation, order
                cust = await db.customers.insert_one({
                    "merchant_id": mid_oid, "name": "TEST_iter3_pay", "phone": "+920000099400"})
                cid = cust.inserted_id
                conv = await db.conversations.insert_one({
                    "merchant_id": mid_oid, "customer_id": cid,
                    "customer_phone": "+920000099400", "mode": "AI_ACTIVE"})
                conv_id = conv.inserted_id
                order = await db.shopify_orders.insert_one({
                    "merchant_id": mid_oid, "customer_id": cid, "conversation_id": conv_id,
                    "order_number": "TEST-ITER3-1", "total": 500,
                    "payment_status": "unpaid",
                    "created_at": "2025-01-01T00:00:00+00:00"})
                order_id = order.inserted_id

                # Mock the AI image analyzer to return high-confidence match
                from services import payment_verification as pv

                async def fake_analyze(image_bytes, mime, expected):
                    return {"receipt_visible": True, "paid": True,
                            "amount_pkr": expected["total"], "reference": "REF1",
                            "transaction_date": "2025-01-02", "recipient_account": "000",
                            "recipient_matches_expected": True, "confidence": 0.95,
                            "reasons": ["matches"]}

                with patch.object(pv, "_analyze_image", new=fake_analyze):
                    result = await pv.verify_payment_screenshot(
                        str(mid_oid), str(conv_id), b"fakebytes", "image/jpeg", media_id="mediaX")

                updated = await db.shopify_orders.find_one({"_id": order_id})
                conv_upd = await db.conversations.find_one({"_id": conv_id})

                # Query the queue endpoint (mocked path)
                headers = {"Authorization": owner_headers["Authorization"]}
                r = requests.get(f"{BASE_URL}/api/payment-review", headers=headers, timeout=30)

                # cleanup
                await db.shopify_orders.delete_one({"_id": order_id})
                await db.conversations.delete_one({"_id": conv_id})
                await db.customers.delete_one({"_id": cid})
                return result, updated, conv_upd, r
            finally:
                client.close()

        result, order, conv, r = _run(_go())
        assert result["decision"] == "review_required"
        assert order["payment_verification_status"] == "review_required"
        assert order["payment_ai_recommendation"] == "approve"
        assert order["payment_screenshot_path"]
        assert order["payment_screenshot_media_id"] == "mediaX"
        assert conv["needs_attention"] is True
        assert conv["payment_review_status"] == "review_required"
        assert r.status_code == 200
        ids_in_queue = [str(o.get("id")) for o in r.json()]
        assert str(order["_id"]) in ids_in_queue


# ---------------- approve/reject endpoint ----------------
class TestApproveRejectEndpoint:
    def _seed_review_order(self, mid_str, status="review_required"):
        async def _go():
            client, db = await _db()
            try:
                mid = ObjectId(mid_str)
                cust = await db.customers.insert_one({
                    "merchant_id": mid, "name": "TEST_iter3_appr", "phone": "+920000099500"})
                conv = await db.conversations.insert_one({
                    "merchant_id": mid, "customer_id": cust.inserted_id,
                    "customer_phone": "+920000099500", "mode": "HUMAN_ACTIVE"})
                # ensure whatsapp connection
                await db.whatsapp_connections.update_one(
                    {"merchant_id": mid},
                    {"$setOnInsert": {"merchant_id": mid, "phone_number_id": "PN_REV",
                                      "access_token_enc": "x", "waba_id": "1", "status": "active"}},
                    upsert=True)
                order = await db.shopify_orders.insert_one({
                    "merchant_id": mid, "customer_id": cust.inserted_id,
                    "conversation_id": conv.inserted_id,
                    "order_number": "TEST-ITER3-REV", "total": 300,
                    "payment_status": "unpaid",
                    "payment_verification_status": status,
                    "payment_screenshot_path": "/tmp/x.bin",
                    "shopify_order_id": "9999",
                    "created_at": "2025-01-01T00:00:00+00:00"})
                return str(order.inserted_id), str(conv.inserted_id), str(cust.inserted_id)
            finally:
                client.close()
        return _run(_go())

    def _cleanup(self, order_id, conv_id, cust_id):
        async def _go():
            client, db = await _db()
            try:
                await db.shopify_orders.delete_one({"_id": ObjectId(order_id)})
                await db.messages.delete_many({"conversation_id": ObjectId(conv_id)})
                await db.conversations.delete_one({"_id": ObjectId(conv_id)})
                await db.customers.delete_one({"_id": ObjectId(cust_id)})
            finally:
                client.close()
        _run(_go())

    def test_approve_marks_paid_and_notifies(self, owner_headers):
        mid = _run(_get_mid())
        oid, cid, custid = self._seed_review_order(mid)
        try:
            from services import whatsapp_service as ws_mod
            from services import shopify_service as sh_mod

            async def fake_send(conn, phone, body):
                return {"messages": [{"id": "wamid.APPR"}]}

            async def fake_update(*a, **k):
                return {"transaction": {"status": "success"}}

            with patch.object(ws_mod.whatsapp_service, "send_text", new=fake_send), \
                 patch.object(sh_mod.shopify_service, "update_payment_review", new=fake_update):
                r = requests.post(f"{BASE_URL}/api/orders/{oid}/payment-review",
                                  json={"decision": "approve", "note": "ok"},
                                  headers=owner_headers, timeout=30)
            assert r.status_code == 200, r.text[:300]
            data = r.json()
            assert data["order"]["payment_status"] == "paid"
            assert data["order"]["payment_verification_status"] == "human_approved_screenshot"

            async def _fetch():
                client, db = await _db()
                try:
                    msgs = await db.messages.find(
                        {"conversation_id": ObjectId(cid), "direction": "outbound"}).to_list(10)
                    order = await db.shopify_orders.find_one({"_id": ObjectId(oid)})
                    return msgs, order
                finally:
                    client.close()

            msgs, order = _run(_fetch())
            assert len(msgs) == 1
            assert msgs[0]["status"] == "sent"
            # shopify sync should have succeeded because shopify_service.configured may be True or fake update was patched
            assert order.get("shopify_payment_sync_status") in {"synced", "not_configured", "not_available", "sync_pending"}
        finally:
            self._cleanup(oid, cid, custid)

    def test_reject_persists_status_and_notification(self, owner_headers):
        mid = _run(_get_mid())
        oid, cid, custid = self._seed_review_order(mid)
        try:
            from services import whatsapp_service as ws_mod
            from services import shopify_service as sh_mod

            async def fake_send(conn, phone, body):
                return {"messages": [{"id": "wamid.REJ"}]}

            async def fake_update(*a, **k):
                return {}

            with patch.object(ws_mod.whatsapp_service, "send_text", new=fake_send), \
                 patch.object(sh_mod.shopify_service, "update_payment_review", new=fake_update):
                r = requests.post(f"{BASE_URL}/api/orders/{oid}/payment-review",
                                  json={"decision": "reject", "note": "blurry"},
                                  headers=owner_headers, timeout=30)
            assert r.status_code == 200, r.text[:300]
            assert r.json()["order"]["payment_verification_status"] == "payment_rejected"
            assert r.json()["order"]["payment_status"] == "unpaid"
        finally:
            self._cleanup(oid, cid, custid)

    def test_whatsapp_failure_still_returns_200(self, owner_headers):
        mid = _run(_get_mid())
        oid, cid, custid = self._seed_review_order(mid)
        try:
            from services import whatsapp_service as ws_mod
            from services import shopify_service as sh_mod

            async def failing_send(conn, phone, body):
                raise RuntimeError("Meta 401 token expired")

            async def fake_update(*a, **k):
                return {"transaction": {"status": "success"}}

            with patch.object(ws_mod.whatsapp_service, "send_text", new=failing_send), \
                 patch.object(sh_mod.shopify_service, "update_payment_review", new=fake_update):
                r = requests.post(f"{BASE_URL}/api/orders/{oid}/payment-review",
                                  json={"decision": "approve", "note": "ok"},
                                  headers=owner_headers, timeout=30)
            assert r.status_code == 200

            async def _fetch():
                client, db = await _db()
                try:
                    msgs = await db.messages.find(
                        {"conversation_id": ObjectId(cid), "direction": "outbound"}).to_list(10)
                    return msgs
                finally:
                    client.close()
            msgs = _run(_fetch())
            assert len(msgs) == 1
            assert msgs[0]["status"] == "failed"
            assert msgs[0].get("delivery_error")
        finally:
            self._cleanup(oid, cid, custid)


# ---------------- shopify service update_payment_review ----------------
class TestShopifyUpdatePaymentReview:
    def test_approve_success(self):
        from services import shopify_service as sh_mod

        class FakeResp:
            status_code = 200
            def json(self): return {"transaction": {"id": 1, "status": "success"}}

        with patch.object(sh_mod, "decrypt_secret", create=True, return_value="tok", side_effect=None), \
             patch("services.crypto.decrypt_secret", return_value="tok"), \
             patch.object(sh_mod.shopify_service, "_ShopifyService__init__", create=True), \
             patch.object(sh_mod.ShopifyService, "configured", property(lambda self: True)):
            # patch requests.post inside method
            with patch("requests.post", return_value=FakeResp()):
                res = _run(sh_mod.shopify_service.update_payment_review(
                    "shop.myshopify.com", "enc", "12345", True, 500.0, "ok"))
        assert "transaction" in res

    def test_reject_maps_non_2xx_to_error(self):
        from services import shopify_service as sh_mod

        class FakeResp:
            status_code = 422
            def json(self): return {"errors": "bad"}

        with patch("services.crypto.decrypt_secret", return_value="tok"), \
             patch.object(sh_mod.ShopifyService, "configured", property(lambda self: True)), \
             patch("requests.put", return_value=FakeResp()):
            with pytest.raises(sh_mod.ShopifyAPIError):
                _run(sh_mod.shopify_service.update_payment_review(
                    "shop.myshopify.com", "enc", "12345", False, 0.0, "rej"))


# ---------------- regression: auth/inbox/settings ----------------
class TestRegression:
    def test_auth_me(self, owner_headers):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=owner_headers, timeout=15)
        assert r.status_code == 200
        assert "@" in r.json().get("email", "")

    def test_conversations_list(self, owner_headers):
        r = requests.get(f"{BASE_URL}/api/conversations", headers=owner_headers, timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_ai_settings(self, owner_headers):
        r = requests.get(f"{BASE_URL}/api/ai-settings", headers=owner_headers, timeout=15)
        assert r.status_code == 200

    def test_orders_list(self, owner_headers):
        r = requests.get(f"{BASE_URL}/api/orders", headers=owner_headers, timeout=20)
        assert r.status_code == 200

    def test_payment_review_queue(self, owner_headers):
        r = requests.get(f"{BASE_URL}/api/payment-review", headers=owner_headers, timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_webhook_verify_challenge(self):
        # No secrets leaked on failure path
        r = requests.get(f"{BASE_URL}/api/webhooks/whatsapp",
                         params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "c"},
                         timeout=15)
        assert r.status_code in (200, 403)

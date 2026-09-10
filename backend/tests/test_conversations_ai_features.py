"""Tests for the Conversations AI fixes: auto-handover, read receipts, unread,
return-to-AI, ai-settings.auto_handover_seconds validation, and persona sanitizer."""
import asyncio
import os
import time
from pathlib import Path

import pytest
import requests
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

# Load backend env (MONGO_URL, DB_NAME) into os.environ BEFORE importing backend modules
backend_env = dotenv_values("/app/backend/.env")
for _k in ("MONGO_URL", "DB_NAME", "EMERGENT_LLM_KEY"):
    if _k not in os.environ and backend_env.get(_k):
        os.environ[_k] = backend_env[_k]
# Make backend importable
import sys
sys.path.insert(0, "/app/backend")
MONGO_URL = os.environ.get("MONGO_URL") or backend_env.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME") or backend_env.get("DB_NAME")

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")).rstrip("/")


# ---------- helpers ----------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


async def _get_db():
    client = AsyncIOMotorClient(MONGO_URL)
    return client, client[DB_NAME]


async def _get_mid_for_email(email: str):
    client, db = await _get_db()
    try:
        user = await db.users.find_one({"email": email})
        assert user, f"user {email} not found"
        return str(user["merchant_id"])
    finally:
        client.close()


async def _pick_or_create_conversation(mid: str):
    """Return a conversation id owned by this merchant, creating one if none exist."""
    client, db = await _get_db()
    try:
        mid_oid = ObjectId(mid)
        conv = await db.conversations.find_one({"merchant_id": mid_oid})
        if conv:
            return str(conv["_id"])
        ins = await db.conversations.insert_one({
            "merchant_id": mid_oid, "customer_name": "TEST_qa_ai_features",
            "customer_phone": "+920000000001", "mode": "AI_ACTIVE",
            "needs_attention": False, "last_message": "hello",
            "last_message_at": "2025-01-01T00:00:00+00:00",
            "created_at": "2025-01-01T00:00:00+00:00",
        })
        return str(ins.inserted_id)
    finally:
        client.close()


async def _insert_inbound(mid: str, cid: str, body: str = "TEST_qa unread ping", created_at: str = None):
    from models import iso_now
    client, db = await _get_db()
    try:
        ts = created_at or iso_now()
        ins = await db.messages.insert_one({
            "conversation_id": ObjectId(cid), "merchant_id": ObjectId(mid),
            "direction": "inbound", "sender": "customer",
            "body": body, "status": "received", "created_at": ts,
        })
        await db.conversations.update_one(
            {"_id": ObjectId(cid), "merchant_id": ObjectId(mid)},
            {"$set": {"last_message": body, "last_message_at": ts, "last_inbound_at": ts},
             "$inc": {"unread_count": 1}})
        return str(ins.inserted_id)
    finally:
        client.close()


async def _get_conv_doc(mid: str, cid: str):
    client, db = await _get_db()
    try:
        return await db.conversations.find_one({"_id": ObjectId(cid), "merchant_id": ObjectId(mid)})
    finally:
        client.close()


async def _cleanup_test_messages(mid: str):
    client, db = await _get_db()
    try:
        await db.messages.delete_many({"merchant_id": ObjectId(mid), "body": {"$regex": "^TEST_qa"}})
    finally:
        client.close()


# ---------- ai-settings.auto_handover_seconds ----------

class TestAISettingsAutoHandover:
    def test_get_returns_auto_handover_seconds(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/ai-settings")
        assert r.status_code == 200
        data = r.json()
        assert "auto_handover_seconds" in data, f"missing key; got: {list(data.keys())}"
        assert isinstance(data["auto_handover_seconds"], (int, float))

    def test_update_valid(self, owner_client):
        r = owner_client.put(f"{BASE_URL}/api/ai-settings", json={"auto_handover_seconds": 45})
        assert r.status_code == 200
        # Verify persisted
        g = owner_client.get(f"{BASE_URL}/api/ai-settings").json()
        assert g["auto_handover_seconds"] == 45

    def test_update_rejects_negative(self, owner_client):
        r = owner_client.put(f"{BASE_URL}/api/ai-settings", json={"auto_handover_seconds": -1})
        assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text[:200]}"

    def test_update_rejects_too_large(self, owner_client):
        r = owner_client.put(f"{BASE_URL}/api/ai-settings", json={"auto_handover_seconds": 3601})
        assert r.status_code == 422

    def test_update_accepts_zero(self, owner_client):
        r = owner_client.put(f"{BASE_URL}/api/ai-settings", json={"auto_handover_seconds": 0})
        assert r.status_code == 200
        assert owner_client.get(f"{BASE_URL}/api/ai-settings").json()["auto_handover_seconds"] == 0


# ---------- takeover / return-to-ai ----------

class TestTakeoverReturnToAI:
    @pytest.fixture(scope="class")
    def cid_and_mid(self, owner_login):
        email = owner_login["data"]["user"]["email"] if "email" in owner_login["data"].get("user", {}) else "techgear008@gmail.com"
        mid = asyncio.run(_get_mid_for_email(email))
        cid = asyncio.run(_pick_or_create_conversation(mid))
        yield cid, mid
        asyncio.run(_cleanup_test_messages(mid))

    def test_takeover_sets_human_mode_and_iso_timestamp(self, owner_client, cid_and_mid):
        cid, mid = cid_and_mid
        r = owner_client.post(f"{BASE_URL}/api/conversations/{cid}/takeover")
        assert r.status_code == 200, r.text
        assert r.json().get("mode") == "HUMAN_ACTIVE"
        doc = asyncio.run(_get_conv_doc(mid, cid))
        assert doc["mode"] == "HUMAN_ACTIVE"
        assert isinstance(doc.get("takeover_at"), str), f"takeover_at should be ISO string, got {type(doc.get('takeover_at'))}"
        # Must parse as ISO
        from datetime import datetime
        datetime.fromisoformat(doc["takeover_at"])

    def test_return_to_ai_unsets_takeover_at(self, owner_client, cid_and_mid):
        cid, mid = cid_and_mid
        owner_client.post(f"{BASE_URL}/api/conversations/{cid}/takeover")
        r = owner_client.post(f"{BASE_URL}/api/conversations/{cid}/return-to-ai")
        assert r.status_code == 200, r.text
        assert r.json().get("mode") == "AI_ACTIVE"
        doc = asyncio.run(_get_conv_doc(mid, cid))
        assert doc["mode"] == "AI_ACTIVE"
        assert "takeover_at" not in doc or doc.get("takeover_at") is None

    def test_return_to_ai_with_latest_inbound_returns_200(self, owner_client, cid_and_mid):
        """Even without LLM key, endpoint should succeed with 200 and mode=AI_ACTIVE."""
        cid, mid = cid_and_mid
        # Insert inbound as latest
        asyncio.run(_insert_inbound(mid, cid, body="TEST_qa return-to-ai trigger"))
        owner_client.post(f"{BASE_URL}/api/conversations/{cid}/takeover")
        r = owner_client.post(f"{BASE_URL}/api/conversations/{cid}/return-to-ai")
        assert r.status_code == 200
        assert r.json()["mode"] == "AI_ACTIVE"


# ---------- read receipts + unread ----------

class TestReadReceiptsAndUnread:
    def test_get_conversation_marks_read_and_resets_unread(self, owner_client, owner_login):
        mid = asyncio.run(_get_mid_for_email("techgear008@gmail.com"))
        cid = asyncio.run(_pick_or_create_conversation(mid))
        msg_id = asyncio.run(_insert_inbound(mid, cid, body="TEST_qa read-receipt msg"))

        pre = asyncio.run(_get_conv_doc(mid, cid))
        assert (pre.get("unread_count") or 0) >= 1

        r = owner_client.get(f"{BASE_URL}/api/conversations/{cid}")
        assert r.status_code == 200
        data = r.json()
        # unread_count now 0 in response
        assert data.get("unread_count", 0) == 0
        # The inserted inbound msg should have read_by_human_at set
        matched = [m for m in data["messages"] if m["id"] == msg_id]
        assert matched, "inserted inbound message not returned"
        assert matched[0].get("read_by_human_at"), "read_by_human_at not stamped"

        post = asyncio.run(_get_conv_doc(mid, cid))
        assert post.get("unread_count", 0) == 0

    def test_list_conversations_returns_unread_count_field(self, owner_client, owner_login):
        mid = asyncio.run(_get_mid_for_email("techgear008@gmail.com"))
        cid = asyncio.run(_pick_or_create_conversation(mid))
        asyncio.run(_insert_inbound(mid, cid, body="TEST_qa unread listing"))
        r = owner_client.get(f"{BASE_URL}/api/conversations")
        assert r.status_code == 200
        rows = r.json()
        target = next((c for c in rows if c["id"] == cid), None)
        assert target is not None
        assert target.get("unread_count", 0) >= 1


# ---------- auto-handover sweep ----------

class TestAutoHandoverSweep:
    def test_sweep_reverts_to_ai_after_timeout(self, owner_client):
        mid = asyncio.run(_get_mid_for_email("techgear008@gmail.com"))
        # Dedicated conversation to avoid xdist races with other tests
        async def _new_conv():
            client, db = await _get_db()
            try:
                ins = await db.conversations.insert_one({
                    "merchant_id": ObjectId(mid), "customer_name": "TEST_qa_sweep",
                    "customer_phone": "+920000000099", "mode": "AI_ACTIVE",
                    "needs_attention": False, "last_message": "hello",
                    "last_message_at": "2025-01-01T00:00:00+00:00",
                    "created_at": "2025-01-01T00:00:00+00:00",
                })
                return str(ins.inserted_id)
            finally:
                client.close()
        cid = asyncio.run(_new_conv())

        # (a) Set tiny timeout
        r = owner_client.put(f"{BASE_URL}/api/ai-settings", json={"auto_handover_seconds": 2})
        assert r.status_code == 200

        # (b) Take over
        r = owner_client.post(f"{BASE_URL}/api/conversations/{cid}/takeover")
        assert r.status_code == 200

        # Force takeover_at to a past ISO string (>2s ago)
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        async def _set_past():
            client, db = await _get_db()
            try:
                await db.conversations.update_one(
                    {"_id": ObjectId(cid), "merchant_id": ObjectId(mid)},
                    {"$set": {"takeover_at": past}})
            finally:
                client.close()
        asyncio.run(_set_past())

        # (c) Insert unread inbound
        asyncio.run(_insert_inbound(mid, cid, body="TEST_qa sweep trigger"))

        # (d) Wait
        time.sleep(3)

        # (e) Trigger sweep via GET /conversations, then re-check
        r = owner_client.get(f"{BASE_URL}/api/conversations")
        assert r.status_code == 200
        # Background task runs after response. Poll for state change.
        doc = None
        for _ in range(10):
            time.sleep(1)
            doc = asyncio.run(_get_conv_doc(mid, cid))
            if doc["mode"] == "AI_ACTIVE":
                break
            # Retrigger sweep in case background task didn't fire on first call
            owner_client.get(f"{BASE_URL}/api/conversations")
        assert doc["mode"] == "AI_ACTIVE", f"expected AI_ACTIVE after sweep, got {doc.get('mode')}"
        assert "takeover_at" not in doc or doc.get("takeover_at") is None

        # Cleanup: delete our dedicated conversation and its messages
        async def _cleanup():
            client, db = await _get_db()
            try:
                await db.messages.delete_many({"conversation_id": ObjectId(cid)})
                await db.conversations.delete_one({"_id": ObjectId(cid)})
            finally:
                client.close()
        asyncio.run(_cleanup())

        # Restore default timeout
        owner_client.put(f"{BASE_URL}/api/ai-settings", json={"auto_handover_seconds": 30})


# ---------- sanitize_reply + system prompt persona ----------

class TestPersonaSanitizer:
    def test_sanitize_strips_emojis(self):
        from services.ai_agent import sanitize_reply
        out = sanitize_reply("Hello 😊 Sir, order confirmed ✅")
        assert "😊" not in out and "✅" not in out
        assert "Hello" in out and "Sir" in out

    def test_sanitize_strips_markdown_asterisks_backticks(self):
        from services.ai_agent import sanitize_reply
        out = sanitize_reply("**bold** and *italic* with `code`")
        assert "*" not in out and "`" not in out
        assert "bold" in out and "italic" in out and "code" in out

    def test_sanitize_strips_stray_underscores_emphasis(self):
        from services.ai_agent import sanitize_reply
        out = sanitize_reply("Say _hello_ nicely")
        assert "_hello_" not in out
        assert "hello" in out

    def test_sanitize_preserves_urls_with_underscores(self):
        from services.ai_agent import sanitize_reply
        url = "https://example.com/path_to_item?id=abc_123"
        out = sanitize_reply(f"Visit {url} for details")
        assert url in out, f"URL mangled: {out}"

    def test_sanitize_preserves_multilingual(self):
        from services.ai_agent import sanitize_reply
        out = sanitize_reply("السلام علیکم Sir, ap ka order ready hai")
        assert "السلام" in out and "ap ka order" in out

    def test_system_prompt_contains_persona_rules(self):
        from services.ai_agent import ai_agent
        # Need a valid merchant_id; use techgear
        mid = asyncio.run(_get_mid_for_email("techgear008@gmail.com"))
        prompt = asyncio.run(ai_agent._build_system_prompt(mid))
        assert "NEVER use emojis" in prompt
        assert "NEVER use markdown formatting" in prompt


# ---------- regression: existing endpoints ----------

class TestRegressionEndpoints:
    def test_login(self, test_credentials):
        r = requests.post(f"{BASE_URL}/api/auth/login", json=test_credentials, timeout=15)
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_list_conversations(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/conversations")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_conversation(self, owner_client):
        mid = asyncio.run(_get_mid_for_email("techgear008@gmail.com"))
        cid = asyncio.run(_pick_or_create_conversation(mid))
        r = owner_client.get(f"{BASE_URL}/api/conversations/{cid}")
        assert r.status_code == 200
        assert "messages" in r.json()

    def test_ai_settings_endpoint(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/ai-settings")
        assert r.status_code == 200

    def test_business_rules_endpoint(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/business-rules")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

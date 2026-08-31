"""Backend API tests for AI WhatsApp Commerce Manager (foundation build)."""
import time
import uuid

import pytest
import requests

from conftest import BASE_URL


# ---------------- Health ----------------
class TestHealth:
    def test_health(self, anon_client):
        r = anon_client.get(f"{BASE_URL}/api/health", timeout=30)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------- Auth ----------------
class TestAuth:
    def test_login_success_and_cookies(self, test_credentials):
        r = requests.post(f"{BASE_URL}/api/auth/login", json=test_credentials, timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["email"] == test_credentials["email"]
        assert d["role"] == "owner"
        assert isinstance(d["access_token"], str) and len(d["access_token"]) > 20
        assert d["merchant_id"]
        # httpOnly cookies
        cookie_hdrs = r.headers.get("set-cookie", "")
        assert "access_token" in cookie_hdrs, f"no access_token cookie: {cookie_hdrs}"
        assert "HttpOnly" in cookie_hdrs or "httponly" in cookie_hdrs

    def test_me(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["merchant_id"]
        assert d["store_name"]
        assert "password_hash" not in d
        assert "_id" not in d

    def test_login_wrong_password(self, test_credentials):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": test_credentials["email"], "password": "WrongPass!123"},
                          timeout=30)
        assert r.status_code in (401, 429), r.text[:200]

    @pytest.mark.parametrize("path", [
        "/api/auth/me", "/api/dashboard", "/api/conversations", "/api/products",
        "/api/orders", "/api/customers", "/api/ai-settings", "/api/integrations/status",
        "/api/ai/tools", "/api/media", "/api/templates", "/api/knowledge-base",
        "/api/business-rules", "/api/analytics/overview",
    ])
    def test_protected_requires_auth(self, anon_client, path):
        r = anon_client.get(f"{BASE_URL}{path}", timeout=30)
        assert r.status_code == 401, f"{path} -> {r.status_code}"

    def test_bcrypt_hash_format(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from security import hash_password, verify_password
        h = hash_password("Abc#12345")
        assert h.startswith("$2b$"), h[:10]
        assert verify_password("Abc#12345", h)
        assert not verify_password("nope", h)


# ---------------- Registration + tenant isolation ----------------
class TestRegistrationIsolation:
    @pytest.fixture(scope="class")
    def new_merchant(self):
        suffix = uuid.uuid4().hex[:8]
        payload = {
            "email": f"TEST_qa_{suffix}@example.com",
            "password": "QaTest#2026",
            "name": "TEST QA Owner",
            "store_name": f"TEST_Store_{suffix}",
        }
        r = requests.post(f"{BASE_URL}/api/auth/register", json=payload, timeout=30)
        assert r.status_code == 200, f"register failed {r.status_code}: {r.text[:300]}"
        d = r.json()
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {d['access_token']}"})
        return {"payload": payload, "data": d, "client": s}

    def test_register_creates_tenant(self, new_merchant):
        d = new_merchant["data"]
        assert d["role"] == "owner"
        assert d["merchant_id"]
        me = new_merchant["client"].get(f"{BASE_URL}/api/auth/me", timeout=30).json()
        assert me["store_name"] == new_merchant["payload"]["store_name"]
        assert me["currency"] == "PKR"

    def test_register_duplicate_email(self, new_merchant):
        r = requests.post(f"{BASE_URL}/api/auth/register", json=new_merchant["payload"], timeout=30)
        assert r.status_code == 400

    def test_default_ai_settings_created(self, new_merchant):
        r = new_merchant["client"].get(f"{BASE_URL}/api/ai-settings", timeout=30)
        assert r.status_code == 200
        s = r.json()
        assert s is not None and s.get("sales_mode")

    @pytest.mark.parametrize("path", ["/api/products", "/api/orders", "/api/conversations",
                                      "/api/customers", "/api/media", "/api/templates",
                                      "/api/knowledge-base", "/api/business-rules"])
    def test_new_tenant_is_empty(self, new_merchant, path):
        r = new_merchant["client"].get(f"{BASE_URL}{path}", timeout=30)
        assert r.status_code == 200
        assert r.json() == [], f"{path} leaked data: {str(r.json())[:200]}"

    def test_new_tenant_dashboard_zeroed(self, new_merchant):
        r = new_merchant["client"].get(f"{BASE_URL}/api/dashboard", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["stats"]["conversations"] == 0
        assert d["stats"]["total_revenue"] == 0
        assert d["recent_orders"] == []
        assert d["top_products"] == []

    def test_cross_tenant_resource_access_blocked(self, new_merchant, owner_client):
        prods = owner_client.get(f"{BASE_URL}/api/products", timeout=30).json()
        assert prods, "seeded tenant has no products"
        pid = prods[0]["id"]
        r = new_merchant["client"].get(f"{BASE_URL}/api/products/{pid}", timeout=30)
        assert r.status_code == 404, f"cross-tenant read allowed: {r.status_code}"

    def test_new_tenant_login_works(self, new_merchant):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": new_merchant["payload"]["email"],
            "password": new_merchant["payload"]["password"]}, timeout=30)
        assert r.status_code == 200


# ---------------- Dashboard / analytics ----------------
class TestDashboardAnalytics:
    def test_dashboard(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/dashboard", timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ["stats", "recent_conversations", "recent_orders", "attention_conversations",
                  "abandoned_opportunities", "low_confidence_events", "top_products"]:
            assert k in d
        st = d["stats"]
        for k in ["conversations", "ai_resolution_rate", "ai_attributed_revenue",
                  "total_revenue", "average_order_value", "abandoned_count"]:
            assert isinstance(st[k], (int, float)), k
        assert st["conversations"] > 0, "seeded demo tenant has no conversations"
        assert st["total_revenue"] > 0

    @pytest.mark.parametrize("path,keys", [
        ("/api/analytics/overview", ["conversations", "total_revenue"]),
        ("/api/analytics/revenue-attribution", ["ai_attributed_revenue", "total_orders"]),
        ("/api/analytics/cross-upsell", ["cross_sell", "upsell"]),
        ("/api/analytics/cod", ["cod_orders", "success_rate"]),
        ("/api/analytics/customers", ["total_customers", "repeat_rate"]),
    ])
    def test_analytics_endpoints(self, owner_client, path, keys):
        r = owner_client.get(f"{BASE_URL}{path}", timeout=30)
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        for k in keys:
            assert k in d, f"{path} missing {k}"


# ---------------- Inbox ----------------
class TestInbox:
    @pytest.fixture(scope="class")
    def conv_id(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/conversations", timeout=30)
        assert r.status_code == 200
        convs = r.json()
        assert convs, "no seeded conversations"
        assert "_id" not in convs[0] and "id" in convs[0]
        return convs[0]["id"]

    def test_conversation_thread(self, owner_client, conv_id):
        r = owner_client.get(f"{BASE_URL}/api/conversations/{conv_id}", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d["messages"], list)
        assert len(d["messages"]) > 0, "conversation has no messages"

    def test_takeover_send_return(self, owner_client, conv_id):
        r = owner_client.post(f"{BASE_URL}/api/conversations/{conv_id}/takeover", timeout=30)
        assert r.status_code == 200 and r.json()["mode"] == "HUMAN_ACTIVE"
        d = owner_client.get(f"{BASE_URL}/api/conversations/{conv_id}", timeout=30).json()
        assert d["mode"] == "HUMAN_ACTIVE"

        body = f"TEST_qa message {uuid.uuid4().hex[:6]}"
        r = owner_client.post(f"{BASE_URL}/api/conversations/{conv_id}/messages",
                             json={"body": body}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        res = r.json()
        assert res["message"]["body"] == body
        assert res["message"]["direction"] == "outbound"
        assert res["delivery"]["delivered"] is False, "claims delivery while WhatsApp not connected"
        # persisted
        d = owner_client.get(f"{BASE_URL}/api/conversations/{conv_id}", timeout=30).json()
        assert any(m["body"] == body for m in d["messages"])

        r = owner_client.post(f"{BASE_URL}/api/conversations/{conv_id}/return-to-ai", timeout=30)
        assert r.status_code == 200 and r.json()["mode"] == "AI_ACTIVE"
        d = owner_client.get(f"{BASE_URL}/api/conversations/{conv_id}", timeout=30).json()
        assert d["mode"] == "AI_ACTIVE"

    def test_conversation_404(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/conversations/{'a'*24}", timeout=30)
        assert r.status_code == 404

    def test_summary_and_suggestion(self, owner_client, conv_id):
        for suffix in ["summary", "suggestion"]:
            r = owner_client.get(f"{BASE_URL}/api/conversations/{conv_id}/{suffix}", timeout=30)
            assert r.status_code == 200, f"{suffix}: {r.text[:200]}"

    def test_ai_tools_registry(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/ai/tools", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["llm_connected"] is False
        assert len(d["tools"]) == 36, f"expected 36 tools, got {len(d['tools'])}"


# ---------------- Commerce: products/orders/customers ----------------
class TestCommerce:
    def test_products_and_detail(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/products", timeout=30)
        assert r.status_code == 200
        prods = r.json()
        assert prods
        pid = prods[0]["id"]
        d = owner_client.get(f"{BASE_URL}/api/products/{pid}", timeout=30).json()
        assert "variants" in d and "media" in d

    def test_product_search(self, owner_client):
        prods = owner_client.get(f"{BASE_URL}/api/products", timeout=30).json()
        term = prods[0]["title"].split()[0]
        r = owner_client.get(f"{BASE_URL}/api/products", params={"q": term}, timeout=30)
        assert r.status_code == 200
        assert all(term.lower() in p["title"].lower() for p in r.json())

    def test_orders(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/orders", timeout=30)
        assert r.status_code == 200
        orders = r.json()
        assert orders
        oid = orders[0]["id"]
        d = owner_client.get(f"{BASE_URL}/api/orders/{oid}", timeout=30)
        assert d.status_code == 200
        assert d.json()["id"] == oid

    def test_customer_detail_with_cod(self, owner_client):
        custs = owner_client.get(f"{BASE_URL}/api/customers", timeout=30).json()
        assert custs
        cid = custs[0]["id"]
        r = owner_client.get(f"{BASE_URL}/api/customers/{cid}", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["cod_risk"]["level"] in ("LOW", "MEDIUM", "HIGH")
        assert "orders" in d

    def test_abandoned_and_relationships(self, owner_client):
        for p in ["/api/abandoned-orders", "/api/product-relationships"]:
            r = owner_client.get(f"{BASE_URL}{p}", timeout=30)
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    def test_media_crud(self, owner_client):
        payload = {"filename": "TEST_qa_image.jpg", "media_type": "image",
                   "url": "https://example.com/TEST_qa_image.jpg",
                   "tags": ["TEST_qa"], "description": "qa media", "color": "Blue", "size": "M"}
        r = owner_client.post(f"{BASE_URL}/api/media", json=payload, timeout=30)
        assert r.status_code == 200, r.text[:300]
        m = r.json()
        assert m["filename"] == payload["filename"]
        mid = m["id"]
        listed = owner_client.get(f"{BASE_URL}/api/media", timeout=30).json()
        assert any(x["id"] == mid for x in listed)
        assert owner_client.delete(f"{BASE_URL}/api/media/{mid}", timeout=30).status_code == 200
        listed = owner_client.get(f"{BASE_URL}/api/media", timeout=30).json()
        assert not any(x["id"] == mid for x in listed)


# ---------------- Deterministic tools ----------------
class TestDeterministicTools:
    def test_address_incomplete(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/tools/validate-address",
                              json={"address": "Ali Raza, Johar Town Lahore"}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "INCOMPLETE", d
        assert "house/street number" in d["missing"]

    def test_address_valid(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/tools/validate-address", json={
            "address": "House 24, Street 7, Block C, Johar Town, Lahore"}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "VALID", d
        assert d["valid"] is True
        assert d["detected_city"] == "lahore"

    def test_address_placeholder(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/tools/validate-address", json={"address": "n/a"}, timeout=30)
        assert r.status_code == 200
        assert r.json()["status"] == "INCOMPLETE"

    @pytest.mark.parametrize("payload", [
        {"previous_orders": 0, "delivered_orders": 0, "cancelled_orders": 0,
         "refused_orders": 0, "failed_deliveries": 0},
        {"previous_orders": 10, "delivered_orders": 10, "cancelled_orders": 0,
         "refused_orders": 0, "failed_deliveries": 0},
        {"previous_orders": 5, "delivered_orders": 1, "cancelled_orders": 2,
         "refused_orders": 2, "failed_deliveries": 3},
    ])
    def test_cod_risk(self, owner_client, payload):
        r = owner_client.post(f"{BASE_URL}/api/tools/cod-risk", json=payload, timeout=30)
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        assert d["level"] in ("LOW", "MEDIUM", "HIGH"), d

    def test_cod_risk_high_for_bad_history(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/tools/cod-risk", json={
            "previous_orders": 6, "delivered_orders": 0, "cancelled_orders": 3,
            "refused_orders": 3, "failed_deliveries": 4}, timeout=30)
        assert r.json()["level"] == "HIGH", r.json()


# ---------------- Merchant settings ----------------
class TestMerchantSettings:
    def test_merchant_profile(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/merchant", timeout=30)
        assert r.status_code == 200
        assert r.json()["currency"] == "PKR"

    def test_ai_settings_persist(self, owner_client):
        orig = owner_client.get(f"{BASE_URL}/api/ai-settings", timeout=30).json()
        target = "AGGRESSIVE_SALES" if orig.get("sales_mode") != "AGGRESSIVE_SALES" else "SUPPORT_ONLY"
        r = owner_client.put(f"{BASE_URL}/api/ai-settings",
                             json={"sales_mode": target, "cross_selling": False}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["sales_mode"] == target
        assert r.json()["cross_selling"] is False
        again = owner_client.get(f"{BASE_URL}/api/ai-settings", timeout=30).json()
        assert again["sales_mode"] == target
        assert again["cross_selling"] is False
        # restore
        owner_client.put(f"{BASE_URL}/api/ai-settings",
                         json={"sales_mode": orig.get("sales_mode", "HELPFUL_SALES"),
                               "cross_selling": orig.get("cross_selling", True)}, timeout=30)

    def test_ai_settings_rejects_invalid_sales_mode(self, owner_client):
        r = owner_client.put(f"{BASE_URL}/api/ai-settings",
                             json={"sales_mode": "NOT_A_MODE"}, timeout=30)
        try:
            assert r.status_code in (400, 422), f"accepted invalid sales_mode -> {r.status_code}"
        finally:
            owner_client.put(f"{BASE_URL}/api/ai-settings",
                             json={"sales_mode": "HELPFUL_SALES"}, timeout=30)

    def test_business_rules_crud(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/business-rules",
                              json={"instruction": "TEST_qa never promise same-day delivery",
                                    "category": "policy"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        rid = r.json()["id"]
        assert any(x["id"] == rid for x in owner_client.get(f"{BASE_URL}/api/business-rules", timeout=30).json())
        assert owner_client.delete(f"{BASE_URL}/api/business-rules/{rid}", timeout=30).status_code == 200
        assert not any(x["id"] == rid for x in owner_client.get(f"{BASE_URL}/api/business-rules", timeout=30).json())

    def test_knowledge_base_crud(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/knowledge-base",
                              json={"title": "TEST_qa Returns", "category": "policy",
                                    "content": "7 day returns"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        did = r.json()["id"]
        assert any(x["id"] == did for x in owner_client.get(f"{BASE_URL}/api/knowledge-base", timeout=30).json())
        assert owner_client.delete(f"{BASE_URL}/api/knowledge-base/{did}", timeout=30).status_code == 200

    def test_template_create(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/templates", json={
            "name": f"test_qa_tpl_{uuid.uuid4().hex[:6]}", "use_case": "order_confirmation",
            "language": "en", "category": "UTILITY", "body": "Hello {{1}}, your order is confirmed."},
            timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["approval_status"] == "PENDING"
        assert any(x["id"] == d["id"] for x in owner_client.get(f"{BASE_URL}/api/templates", timeout=30).json())

    def test_audit_logs(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/audit-logs", timeout=30)
        assert r.status_code == 200
        logs = r.json()
        assert isinstance(logs, list)
        assert logs, "audit log empty though actions were performed (merchant_id type mismatch?)"


# ---------------- Integrations ----------------
class TestIntegrations:
    def test_status_not_configured(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/integrations/status", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["shopify"]["server_configured"] is False
        assert d["shopify"]["connected"] is False
        assert d["whatsapp"]["server_configured"] is False

    def test_shopify_connect_503(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/integrations/shopify/connect",
                              json={"shop_domain": "test-qa-store.myshopify.com"}, timeout=30)
        assert r.status_code == 503, f"{r.status_code}: {r.text[:200]}"
        assert "configur" in r.json()["detail"].lower()

    def test_shopify_sync_without_connection(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/integrations/shopify/sync", timeout=30)
        assert r.status_code in (400, 503), r.status_code

    def test_whatsapp_connect_and_disconnect(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/integrations/whatsapp/connect", json={
            "waba_id": "TEST_qa_waba", "phone_number_id": "TEST_qa_phone",
            "display_phone_number": "+92 300 0000000", "access_token": "TEST_qa_token_secret"},
            timeout=30)
        assert r.status_code == 200, r.text[:300]
        st = owner_client.get(f"{BASE_URL}/api/integrations/status", timeout=30).json()
        assert st["whatsapp"]["connected"] is True
        assert st["whatsapp"]["display_phone_number"] == "+92 300 0000000"
        assert "access_token" not in str(st["whatsapp"])
        # cleanup
        assert owner_client.post(f"{BASE_URL}/api/integrations/whatsapp/disconnect",
                                 timeout=30).status_code == 200
        st = owner_client.get(f"{BASE_URL}/api/integrations/status", timeout=30).json()
        assert st["whatsapp"]["connected"] is False

    def test_webhook_verify_handshake(self, anon_client):
        import os as _os
        from dotenv import dotenv_values as _dv
        env = _dv("/app/backend/.env")
        token = env.get("META_VERIFY_TOKEN") or env.get("WHATSAPP_VERIFY_TOKEN") or _os.environ.get("META_VERIFY_TOKEN")
        if not token:
            pytest.skip("WHATSAPP_VERIFY_TOKEN not set in backend/.env")
        r = anon_client.get(f"{BASE_URL}/api/webhooks/whatsapp", params={
            "hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "12345"}, timeout=30)
        assert r.status_code == 200, r.text[:200]
        assert r.text.strip() == "12345"

    def test_webhook_verify_bad_token(self, anon_client):
        r = anon_client.get(f"{BASE_URL}/api/webhooks/whatsapp", params={
            "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}, timeout=30)
        assert r.status_code == 403

    def test_webhook_post_bad_signature(self, anon_client):
        r = anon_client.post(f"{BASE_URL}/api/webhooks/whatsapp", json={"entry": []},
                             headers={"X-Hub-Signature-256": "sha256=bad"}, timeout=30)
        assert r.status_code == 401


# ---------------- Brute-force lockout (runs last) ----------------
class TestBruteForceLockout:
    def test_lockout_after_5_failures(self):
        email = f"TEST_qa_lock_{uuid.uuid4().hex[:8]}@example.com"
        requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "QaLock#2026", "name": "TEST Lock",
            "store_name": "TEST_Lock_Store"}, timeout=30)
        codes = []
        for _ in range(12):
            r = requests.post(f"{BASE_URL}/api/auth/login",
                              json={"email": email, "password": "bad-pass"}, timeout=30)
            codes.append(r.status_code)
            time.sleep(0.2)
        assert 429 in codes, f"no lockout after repeated failures: {codes}"
        # correct password should also be blocked while locked
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": email, "password": "QaLock#2026"}, timeout=30)
        assert r.status_code == 429, f"lock bypassed with correct password: {r.status_code}"

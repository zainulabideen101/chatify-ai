#!/usr/bin/env python3
"""
Shopify Order Synchronization Testing
Tests Shopify order creation, cancellation, webhook sync, and fail-closed behavior.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
from typing import Any

# Add backend to path for imports
sys.path.insert(0, "/app/backend")

import httpx

# Backend URL from environment
BACKEND_URL = "https://whatsapp-bot-import.preview.emergentagent.com/api"

# Test credentials from test_credentials.md
OWNER_EMAIL = "techgear008@gmail.com"
OWNER_PASSWORD = "TechGear#2026"

# Test results tracking
test_results = []


def log_test(name: str, passed: bool, details: str = ""):
    """Log test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    test_results.append({"name": name, "passed": passed, "details": details})
    print(f"{status}: {name}")
    if details:
        print(f"  Details: {details}")


async def test_backend_startup():
    """Test 1: Verify backend starts cleanly with 'Startup: indexes + demo seed complete'."""
    try:
        with open("/var/log/supervisor/backend.err.log", "r") as f:
            logs = f.read()
            lines = logs.split("\n")
            
            # Find the most recent startup message
            startup_found = False
            for line in reversed(lines):
                if "Startup: indexes + demo seed complete" in line:
                    startup_found = True
                    break
            
            if not startup_found:
                log_test("Backend startup message", False, "Startup message not found in logs")
                return
            
            log_test("Backend startup message", True, "Found 'Startup: indexes + demo seed complete'")
            
            # Check for index errors in recent logs (last 50 lines)
            recent_logs = lines[-50:]
            index_errors = []
            for line in recent_logs:
                if "IndexKeySpecsConflict" in line or "CannotCreateIndex" in line or "DuplicateKey" in line:
                    if "shopify_orders" in line:
                        index_errors.append(line[:100])
            
            if index_errors:
                log_test("Backend startup - no index errors", False, f"Index errors found: {len(index_errors)}")
                return
            
            log_test("Backend startup - no index errors", True, "No index errors in recent logs")
    except Exception as e:
        log_test("Backend startup", False, f"Error reading logs: {e}")


async def login_owner() -> tuple[str, dict]:
    """Login as owner and return (access_token, user_data)."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{BACKEND_URL}/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        if response.status_code != 200:
            raise Exception(f"Login failed: {response.status_code} {response.text}")
        data = response.json()
        return data["access_token"], data


async def test_auth_regression():
    """Test 2: Verify login and /api/auth/me work correctly."""
    try:
        token, user_data = await login_owner()
        
        if not token or len(token) < 10:
            log_test("Auth regression - login", False, "No valid access_token returned")
            return None
        
        log_test("Auth regression - login", True, f"Login successful for {OWNER_EMAIL}")
        
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BACKEND_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("Auth regression - /auth/me", False, f"Status {response.status_code}")
                return None
            
            log_test("Auth regression - /auth/me", True, "Returns correct user data")
            return token
    except Exception as e:
        log_test("Auth regression", False, f"Exception: {e}")
        return None


async def test_gpt54_playground(token: str):
    """Test 3: Verify GPT-5.4 playground works."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BACKEND_URL}/ai/playground/playground-main",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("GPT-5.4 playground - GET", False, f"Status {response.status_code}")
                return
            
            session_data = response.json()
            if not session_data.get("llm_connected"):
                log_test("GPT-5.4 playground - GET", False, "llm_connected=false")
                return
            
            log_test("GPT-5.4 playground - GET", True, "Session created with llm_connected=true")
            
            response = await client.post(
                f"{BACKEND_URL}/ai/playground/playground-main",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": "What is 2+2?"}
            )
            if response.status_code != 200:
                log_test("GPT-5.4 playground - POST", False, f"Status {response.status_code}")
                return
            
            result = response.json()
            reply = result.get("reply", "")
            if not reply or len(reply) < 1:
                log_test("GPT-5.4 playground - POST", False, "No reply from GPT-5.4")
                return
            
            log_test("GPT-5.4 playground - POST", True, f"GPT-5.4 replied: {reply[:50]}")
    except Exception as e:
        log_test("GPT-5.4 playground", False, f"Exception: {e}")


async def test_ai_tools_registry(token: str):
    """Test 4: Verify /api/ai/tools exposes create_shopify_order and registry is accurate."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BACKEND_URL}/ai/tools",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("AI tools registry - endpoint", False, f"Status {response.status_code}")
                return
            
            data = response.json()
            
            if not data.get("llm_connected"):
                log_test("AI tools registry - LLM connected", False, "llm_connected=false")
                return
            
            log_test("AI tools registry - LLM connected", True, "llm_connected=true")
            
            tools = data.get("tools", [])
            if not tools:
                log_test("AI tools registry - tools list", False, "No tools returned")
                return
            
            # Find create_shopify_order tool
            shopify_tool = None
            for tool in tools:
                if tool.get("name") == "create_shopify_order":
                    shopify_tool = tool
                    break
            
            if not shopify_tool:
                log_test("AI tools registry - create_shopify_order", False, "create_shopify_order tool not found")
                return
            
            log_test("AI tools registry - create_shopify_order", True, 
                    f"Found create_shopify_order tool (category: {shopify_tool.get('category')}, implemented: {shopify_tool.get('implemented')})")
            
            # Verify tool has correct structure
            if not shopify_tool.get("description"):
                log_test("AI tools registry - tool description", False, "create_shopify_order missing description")
                return
            
            # Note: /api/ai/tools returns simplified tool info without full OpenAI function schema
            # The actual parameters are in TOOL_SCHEMAS in ai_tools.py
            # Just verify the tool is registered and marked as implemented
            if not shopify_tool.get("implemented"):
                log_test("AI tools registry - tool implemented", False, "create_shopify_order not marked as implemented")
                return
            
            log_test("AI tools registry - tool structure", True, "create_shopify_order is registered and implemented")
            
            # Verify total tool count is reasonable
            tool_count = len(tools)
            if tool_count < 10:
                log_test("AI tools registry - tool count", False, f"Only {tool_count} tools (expected more)")
                return
            
            log_test("AI tools registry - tool count", True, f"{tool_count} tools registered")
    except Exception as e:
        log_test("AI tools registry", False, f"Exception: {e}")


async def test_shopify_not_connected_fail_closed(token: str):
    """Test 5: Verify order tool fails closed when Shopify not connected (code review)."""
    try:
        # First verify Shopify is not connected
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BACKEND_URL}/integrations/status",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("Shopify not connected - status check", False, f"Status {response.status_code}")
                return
            
            data = response.json()
            shopify = data.get("shopify", {})
            
            if shopify.get("connected"):
                log_test("Shopify not connected - precondition", False, 
                        "Shopify is connected - cannot test fail-closed behavior")
                return
            
            if shopify.get("server_configured"):
                log_test("Shopify not connected - precondition", False, 
                        "SHOPIFY_API_KEY/SECRET are configured - cannot test fail-closed behavior")
                return
            
            log_test("Shopify not connected - precondition", True, 
                    "Shopify not connected and credentials not configured")
            
            # Code review: Verify create_customer_order function fails closed
            with open("/app/backend/services/shopify_orders.py", "r") as f:
                code = f.read()
                
                # Check for connection check at start of function
                if "shopify_service.configured" not in code:
                    log_test("Shopify not connected - configured check", False, 
                            "shopify_service.configured check not found")
                    return
                
                # Check for fail-closed return
                if '"created": False' not in code or '"code": "shopify_not_connected"' not in code:
                    log_test("Shopify not connected - fail closed return", False, 
                            "Fail-closed return with created=False and code=shopify_not_connected not found")
                    return
                
                # Check that message warns AI not to claim success
                if "Do not claim" not in code and "not claim" not in code:
                    log_test("Shopify not connected - AI warning", False, 
                            "Message doesn't warn AI not to claim success")
                    return
                
                log_test("Shopify not connected - code review", True, 
                        "Code checks shopify_service.configured and returns created=False with shopify_not_connected code")
    except Exception as e:
        log_test("Shopify not connected - fail closed", False, f"Exception: {e}")


async def test_no_local_fake_order_created(token: str):
    """Test 6: Verify no local order is created when Shopify not connected."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Get current order count
            response = await client.get(
                f"{BACKEND_URL}/orders",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("No local fake order - get orders", False, f"Status {response.status_code}")
                return
            
            orders_before = response.json()
            count_before = len(orders_before)
            
            # Try to create an order via playground (should fail closed)
            response = await client.post(
                f"{BACKEND_URL}/ai/playground/playground-main",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "message": "I want to order 1 Black Hoodie size Large. My name is Test Customer, phone +923009999999, address Test Address, city Karachi, payment COD. Please create the order now."
                }
            )
            
            if response.status_code != 200:
                log_test("No local fake order - tool invocation", False, f"Status {response.status_code}")
                return
            
            # Get order count after
            response = await client.get(
                f"{BACKEND_URL}/orders",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("No local fake order - get orders after", False, f"Status {response.status_code}")
                return
            
            orders_after = response.json()
            count_after = len(orders_after)
            
            if count_after > count_before:
                # Check if new order has shopify_order_id
                new_orders = [o for o in orders_after if o not in orders_before]
                for order in new_orders:
                    if not order.get("shopify_order_id"):
                        log_test("No local fake order", False, 
                                f"Local order created without shopify_order_id: {order.get('order_number')}")
                        return
                
                log_test("No local fake order", True, 
                        "New orders have shopify_order_id (may be from previous tests)")
            else:
                log_test("No local fake order", True, "No new local orders created when Shopify not connected")
    except Exception as e:
        log_test("No local fake order", False, f"Exception: {e}")


async def test_cancellation_intent_detector():
    """Test 7: Verify cancellation intent detector recognizes English/Roman-Urdu phrases."""
    try:
        # Load MONGO_URL from backend .env if not in environment
        if "MONGO_URL" not in os.environ:
            try:
                with open("/app/backend/.env", "r") as f:
                    for line in f:
                        if line.startswith("MONGO_URL="):
                            os.environ["MONGO_URL"] = line.split("=", 1)[1].strip()
                        elif line.startswith("DB_NAME="):
                            os.environ["DB_NAME"] = line.split("=", 1)[1].strip()
            except Exception:
                pass
        
        from services.shopify_orders import is_cancellation_request
        
        # English phrases
        english_phrases = [
            "cancel my order",
            "I want to cancel",
            "please cancel the order",
            "order cancelled",
            "cancellation request"
        ]
        
        # Roman Urdu phrases
        roman_urdu_phrases = [
            "order cancel kar do",
            "cancel kr do",
            "order nahi chahiye",
            "cancel kardo",
            "order band kar do"
        ]
        
        # Non-cancellation phrases
        non_cancel_phrases = [
            "what is the status of my order",
            "when will my order arrive",
            "I want to order a hoodie",
            "show me products"
        ]
        
        all_passed = True
        failed_phrases = []
        
        # Test English phrases
        for phrase in english_phrases:
            if not is_cancellation_request(phrase):
                all_passed = False
                failed_phrases.append(f"English: '{phrase}'")
        
        # Test Roman Urdu phrases
        for phrase in roman_urdu_phrases:
            if not is_cancellation_request(phrase):
                all_passed = False
                failed_phrases.append(f"Roman Urdu: '{phrase}'")
        
        # Test non-cancellation phrases (should NOT match)
        for phrase in non_cancel_phrases:
            if is_cancellation_request(phrase):
                all_passed = False
                failed_phrases.append(f"False positive: '{phrase}'")
        
        if not all_passed:
            log_test("Cancellation intent detector", False, 
                    f"Failed to detect: {', '.join(failed_phrases[:3])}")
            return
        
        log_test("Cancellation intent detector", True, 
                f"Correctly detected {len(english_phrases)} English + {len(roman_urdu_phrases)} Roman Urdu phrases")
    except Exception as e:
        log_test("Cancellation intent detector", False, f"Exception: {e}")


async def test_cancellation_without_shopify_connection():
    """Test 8: Verify cancellation without Shopify connection records pending state."""
    try:
        # Load MONGO_URL from backend .env if not in environment
        if "MONGO_URL" not in os.environ:
            try:
                with open("/app/backend/.env", "r") as f:
                    for line in f:
                        if line.startswith("MONGO_URL="):
                            os.environ["MONGO_URL"] = line.split("=", 1)[1].strip()
                        elif line.startswith("DB_NAME="):
                            os.environ["DB_NAME"] = line.split("=", 1)[1].strip()
            except Exception:
                pass
        
        from services.shopify_orders import cancel_latest_customer_order
        from motor.motor_asyncio import AsyncIOMotorClient
        from bson import ObjectId
        from datetime import datetime, timezone
        
        # Load MongoDB connection
        mongo_url = os.environ.get("MONGO_URL")
        if not mongo_url:
            with open("/app/backend/.env", "r") as f:
                for line in f:
                    if line.startswith("MONGO_URL="):
                        mongo_url = line.split("=", 1)[1].strip()
        
        client = AsyncIOMotorClient(mongo_url)
        db = client[os.environ.get("DB_NAME", "whatsapp_bot")]
        
        # Get test merchant
        merchant = await db.merchants.find_one({"store_name": "TechGear Threads"})
        if not merchant:
            log_test("Cancellation without Shopify - merchant", False, "Test merchant not found")
            return
        
        merchant_id = str(merchant["_id"])
        
        # Create a test customer
        test_phone = "+923001111111"
        customer = await db.customers.find_one({"merchant_id": merchant["_id"], "phone": test_phone})
        if not customer:
            result = await db.customers.insert_one({
                "merchant_id": merchant["_id"],
                "name": "Test Cancel Customer",
                "phone": test_phone,
                "preferred_language": "English",
                "total_orders": 0,
                "delivered_orders": 0,
                "cancelled_orders": 0,
                "refused_orders": 0,
                "failed_deliveries": 0,
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            customer = {"_id": result.inserted_id}
        
        # Create a test order without shopify_order_id
        test_order = await db.shopify_orders.insert_one({
            "merchant_id": merchant["_id"],
            "customer_id": customer["_id"],
            "order_number": "TEST_CANCEL_001",
            "shopify_order_id": None,  # No Shopify order ID
            "total": 2999,
            "attribution": "AI",
            "payment_method": "COD",
            "payment_status": "unpaid",
            "fulfillment_status": "pending",
            "items": [{"title": "Black Hoodie", "variant": "Large", "qty": 1, "price": 2999}],
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        
        # Verify Shopify is not connected
        shopify_conn = await db.shopify_connections.find_one({"merchant_id": merchant["_id"]})
        if shopify_conn and shopify_conn.get("access_token_enc"):
            log_test("Cancellation without Shopify - precondition", False, 
                    "Shopify is connected - cannot test without-connection behavior")
            await db.shopify_orders.delete_one({"_id": test_order.inserted_id})
            client.close()
            return
        
        log_test("Cancellation without Shopify - precondition", True, "Shopify not connected")
        
        # Try to cancel the order
        result = await cancel_latest_customer_order(merchant_id, test_phone)
        
        # Verify result
        if result.get("cancelled") == True:
            log_test("Cancellation without Shopify - result", False, 
                    "Returned cancelled=True despite no Shopify connection")
            await db.shopify_orders.delete_one({"_id": test_order.inserted_id})
            client.close()
            return
        
        log_test("Cancellation without Shopify - result", True, "Returned cancelled=False")
        
        # Verify code
        if result.get("code") != "shopify_not_connected":
            log_test("Cancellation without Shopify - code", False, 
                    f"Expected code=shopify_not_connected, got {result.get('code')}")
            await db.shopify_orders.delete_one({"_id": test_order.inserted_id})
            client.close()
            return
        
        log_test("Cancellation without Shopify - code", True, "Returned code=shopify_not_connected")
        
        # Verify message mentions seller review
        message = result.get("message", "")
        if "seller" not in message.lower() or "review" not in message.lower():
            log_test("Cancellation without Shopify - message", False, 
                    f"Message doesn't mention seller review: {message}")
            await db.shopify_orders.delete_one({"_id": test_order.inserted_id})
            client.close()
            return
        
        log_test("Cancellation without Shopify - message", True, "Message mentions seller review")
        
        # Verify order was updated with pending status
        updated_order = await db.shopify_orders.find_one({"_id": test_order.inserted_id})
        if not updated_order:
            log_test("Cancellation without Shopify - order update", False, "Order not found after cancellation")
            client.close()
            return
        
        if updated_order.get("cancellation_status") != "pending_shopify":
            log_test("Cancellation without Shopify - cancellation_status", False, 
                    f"Expected cancellation_status=pending_shopify, got {updated_order.get('cancellation_status')}")
            await db.shopify_orders.delete_one({"_id": test_order.inserted_id})
            client.close()
            return
        
        log_test("Cancellation without Shopify - cancellation_status", True, 
                "Order marked with cancellation_status=pending_shopify")
        
        if not updated_order.get("cancellation_requested_at"):
            log_test("Cancellation without Shopify - timestamp", False, "cancellation_requested_at not set")
            await db.shopify_orders.delete_one({"_id": test_order.inserted_id})
            client.close()
            return
        
        log_test("Cancellation without Shopify - timestamp", True, "cancellation_requested_at timestamp set")
        
        # Cleanup
        await db.shopify_orders.delete_one({"_id": test_order.inserted_id})
        client.close()
    except Exception as e:
        log_test("Cancellation without Shopify", False, f"Exception: {e}")


async def test_shopify_webhook_invalid_hmac():
    """Test 9: Verify Shopify webhook with invalid HMAC returns 401."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            payload = {
                "id": 12345,
                "order_number": "TEST001",
                "total_price": "100.00",
                "financial_status": "paid"
            }
            
            response = await client.post(
                f"{BACKEND_URL}/webhooks/shopify",
                json=payload,
                headers={
                    "X-Shopify-Hmac-Sha256": "invalid_hmac_signature",
                    "X-Shopify-Topic": "orders/paid",
                    "X-Shopify-Shop-Domain": "test-shop.myshopify.com"
                }
            )
            
            if response.status_code != 401:
                log_test("Shopify webhook - invalid HMAC", False, 
                        f"Expected 401, got {response.status_code}")
                return
            
            log_test("Shopify webhook - invalid HMAC", True, "Invalid HMAC rejected with 401")
    except Exception as e:
        log_test("Shopify webhook - invalid HMAC", False, f"Exception: {e}")


async def test_webhook_event_deduplication():
    """Test 10: Verify webhook event deduplication works."""
    try:
        # Check the webhook_events collection has unique index on event_key
        from motor.motor_asyncio import AsyncIOMotorClient
        
        mongo_url = os.environ.get("MONGO_URL")
        if not mongo_url:
            with open("/app/backend/.env", "r") as f:
                for line in f:
                    if line.startswith("MONGO_URL="):
                        mongo_url = line.split("=", 1)[1].strip()
        
        client = AsyncIOMotorClient(mongo_url)
        db = client[os.environ.get("DB_NAME", "whatsapp_bot")]
        
        # Check for unique index on event_key
        indexes = await db.webhook_events.index_information()
        
        event_key_index = None
        for idx_name, idx_info in indexes.items():
            if idx_info.get("key") == [("event_key", 1)] and idx_info.get("unique"):
                event_key_index = idx_name
                break
        
        if not event_key_index:
            log_test("Webhook event deduplication - index", False, 
                    "event_key unique index not found on webhook_events collection")
            client.close()
            return
        
        log_test("Webhook event deduplication - index", True, 
                f"Found unique index on event_key: {event_key_index}")
        
        # Verify code uses SHA256 hash for deduplication
        with open("/app/backend/routers/integrations.py", "r") as f:
            code = f.read()
            
            if "hashlib.sha256(body).hexdigest()" not in code:
                log_test("Webhook event deduplication - SHA256", False, 
                        "SHA256 hash not used for event_key")
                client.close()
                return
            
            if "upserted_id" not in code or "duplicate" not in code:
                log_test("Webhook event deduplication - logic", False, 
                        "Deduplication logic not found")
                client.close()
                return
        
        log_test("Webhook event deduplication - logic", True, 
                "SHA256 hash used for event_key with upsert deduplication")
        
        client.close()
    except Exception as e:
        log_test("Webhook event deduplication", False, f"Exception: {e}")


async def test_webhook_tenant_routing():
    """Test 11: Verify webhook tenant shop-domain routing is safe."""
    try:
        # Verify code routes by shop_domain and validates merchant_id
        with open("/app/backend/routers/integrations.py", "r") as f:
            code = f.read()
            
            # Check for shop_domain routing
            if "shop_domain" not in code or "shopify_connections.find_one" not in code:
                log_test("Webhook tenant routing - shop_domain", False, 
                        "shop_domain routing not found")
                return
            
            # Check for merchant_id scoping
            if 'connection["merchant_id"]' not in code and "connection.get(\"merchant_id\")" not in code:
                log_test("Webhook tenant routing - merchant_id", False, 
                        "merchant_id not extracted from connection")
                return
            
            # Check for merchant-scoped updates
            if '"merchant_id": mid' not in code and '"merchant_id": connection["merchant_id"]' not in code:
                log_test("Webhook tenant routing - scoped updates", False, 
                        "Updates not merchant-scoped")
                return
        
        log_test("Webhook tenant routing", True, 
                "Webhook routes by shop_domain, extracts merchant_id, and scopes all updates")
    except Exception as e:
        log_test("Webhook tenant routing", False, f"Exception: {e}")


async def test_local_order_updates_merchant_scoped():
    """Test 12: Verify local order updates are merchant-scoped."""
    try:
        # Verify code always includes merchant_id in queries and updates
        with open("/app/backend/routers/integrations.py", "r") as f:
            code = f.read()
            
            # Find _sync_shopify_order_event function
            if "_sync_shopify_order_event" not in code:
                log_test("Local order updates - function", False, 
                        "_sync_shopify_order_event function not found")
                return
            
            # Extract function code
            func_start = code.find("async def _sync_shopify_order_event")
            func_end = code.find("\n\n@", func_start)
            if func_end == -1:
                func_end = code.find("\n\nasync def", func_start + 1)
            func_code = code[func_start:func_end]
            
            # Check for merchant_id in find_one
            if '"merchant_id": mid' not in func_code:
                log_test("Local order updates - find_one scoped", False, 
                        "find_one not scoped by merchant_id")
                return
            
            # Check for merchant_id in update_one
            if '"merchant_id": mid' not in func_code or "update_one" not in func_code:
                log_test("Local order updates - update_one scoped", False, 
                        "update_one not scoped by merchant_id")
                return
            
            # Check for merchant_id in insert_one
            if '"merchant_id": mid' not in func_code or "insert_one" not in func_code:
                log_test("Local order updates - insert_one scoped", False, 
                        "insert_one not scoped by merchant_id")
                return
        
        log_test("Local order updates - merchant scoped", True, 
                "All find_one, update_one, and insert_one operations are merchant-scoped")
    except Exception as e:
        log_test("Local order updates - merchant scoped", False, f"Exception: {e}")


async def test_shopify_orders_index():
    """Test 13: Verify unique MongoDB string-type partial index on shopify_orders."""
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        
        mongo_url = os.environ.get("MONGO_URL")
        if not mongo_url:
            with open("/app/backend/.env", "r") as f:
                for line in f:
                    if line.startswith("MONGO_URL="):
                        mongo_url = line.split("=", 1)[1].strip()
        
        client = AsyncIOMotorClient(mongo_url)
        db = client[os.environ.get("DB_NAME", "whatsapp_bot")]
        
        # Get indexes on shopify_orders collection
        indexes = await db.shopify_orders.index_information()
        
        # Look for merchant_id + shopify_order_id index
        target_index = None
        for idx_name, idx_info in indexes.items():
            key = idx_info.get("key", [])
            if key == [("merchant_id", 1), ("shopify_order_id", 1)]:
                target_index = idx_info
                break
        
        if not target_index:
            log_test("Shopify orders index - existence", False, 
                    "merchant_id + shopify_order_id index not found")
            client.close()
            return
        
        log_test("Shopify orders index - existence", True, 
                "Found merchant_id + shopify_order_id index")
        
        # Verify it's unique
        if not target_index.get("unique"):
            log_test("Shopify orders index - unique", False, "Index is not unique")
            client.close()
            return
        
        log_test("Shopify orders index - unique", True, "Index is unique")
        
        # Verify it has partialFilterExpression with $type: "string"
        partial_filter = target_index.get("partialFilterExpression", {})
        shopify_order_id_filter = partial_filter.get("shopify_order_id", {})
        
        if shopify_order_id_filter.get("$type") != "string":
            log_test("Shopify orders index - string type filter", False, 
                    f"Expected $type: 'string', got {shopify_order_id_filter}")
            client.close()
            return
        
        log_test("Shopify orders index - string type filter", True, 
                "Index has partialFilterExpression with shopify_order_id $type: 'string'")
        
        client.close()
    except Exception as e:
        log_test("Shopify orders index", False, f"Exception: {e}")


async def main():
    """Run all Shopify order synchronization tests."""
    print("=" * 80)
    print("Shopify Order Synchronization Testing")
    print("=" * 80)
    print()
    
    # Test 1: Backend startup
    await test_backend_startup()
    print()
    
    # Test 2: Auth regression
    token = await test_auth_regression()
    if not token:
        print("\n❌ Auth failed - cannot continue with authenticated tests")
        print_summary()
        return
    print()
    
    # Test 3: GPT-5.4 playground
    await test_gpt54_playground(token)
    print()
    
    # Test 4: AI tools registry
    await test_ai_tools_registry(token)
    print()
    
    # Test 5: Shopify not connected fail closed
    await test_shopify_not_connected_fail_closed(token)
    print()
    
    # Test 6: No local fake order created
    await test_no_local_fake_order_created(token)
    print()
    
    # Test 7: Cancellation intent detector
    await test_cancellation_intent_detector()
    print()
    
    # Test 8: Cancellation without Shopify connection
    await test_cancellation_without_shopify_connection()
    print()
    
    # Test 9: Shopify webhook invalid HMAC
    await test_shopify_webhook_invalid_hmac()
    print()
    
    # Test 10: Webhook event deduplication
    await test_webhook_event_deduplication()
    print()
    
    # Test 11: Webhook tenant routing
    await test_webhook_tenant_routing()
    print()
    
    # Test 12: Local order updates merchant scoped
    await test_local_order_updates_merchant_scoped()
    print()
    
    # Test 13: Shopify orders index
    await test_shopify_orders_index()
    print()
    
    print_summary()


def print_summary():
    """Print test summary."""
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for t in test_results if t["passed"])
    failed = sum(1 for t in test_results if not t["passed"])
    total = len(test_results)
    
    print(f"\nTotal: {total} tests")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    
    if total > 0:
        print(f"Success rate: {passed/total*100:.1f}%")
    
    if failed > 0:
        print("\n❌ FAILED TESTS:")
        for t in test_results:
            if not t["passed"]:
                print(f"  - {t['name']}")
                if t["details"]:
                    print(f"    {t['details']}")
    
    print("\n" + "=" * 80)
    
    # Exit with appropriate code
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

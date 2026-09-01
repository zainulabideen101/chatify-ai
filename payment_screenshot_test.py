#!/usr/bin/env python3
"""
Payment Screenshot Verification Testing.
Tests bank-transfer instructions, GPT-5.4 vision-based screenshot verification, and order status updates.
"""
import asyncio
import base64
import io
import json
import os
import sys
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

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


def create_synthetic_receipt(amount: float, account_number: str, readable: bool = True) -> tuple[bytes, str]:
    """Create a synthetic payment receipt image with visible text."""
    # Create a white background image
    width, height = 600, 800
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    
    if readable:
        # Draw receipt header
        draw.rectangle([(0, 0), (width, 80)], fill='#1a73e8')
        
        # Add text (using default font since custom fonts may not be available)
        y_position = 100
        line_height = 40
        
        texts = [
            "PAYMENT RECEIPT",
            "",
            f"Amount: PKR {amount:.2f}",
            f"Account: {account_number}",
            "Bank: Chatify AI Demo Bank",
            "Account Title: Chatify AI Demo Store",
            "Status: SUCCESSFUL",
            f"Date: 2026-01-15 10:30:00",
            "Reference: TXN123456789",
        ]
        
        for text in texts:
            # Draw text in black
            draw.text((50, y_position), text, fill='black')
            y_position += line_height
    else:
        # Create unreadable receipt (blurred/low quality)
        draw.rectangle([(50, 50), (550, 750)], fill='#cccccc')
        draw.text((100, 100), "Unreadable", fill='#888888')
    
    # Convert to bytes
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    buffer.seek(0)
    return buffer.read(), 'image/jpeg'


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
    """Test 1: Verify login still works."""
    try:
        token, user_data = await login_owner()
        
        if not token or len(token) < 10:
            log_test("Auth regression - login", False, "No valid access_token returned")
            return None
        
        if user_data.get("email") != OWNER_EMAIL:
            log_test("Auth regression - login", False, f"Wrong email: {user_data.get('email')}")
            return None
        
        log_test("Auth regression - login", True, f"Login successful for {OWNER_EMAIL}")
        
        # Test /api/auth/me
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BACKEND_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("Auth regression - /auth/me", False, f"Status {response.status_code}")
                return None
            
            me_data = response.json()
            if me_data.get("email") != OWNER_EMAIL:
                log_test("Auth regression - /auth/me", False, f"Wrong email: {me_data.get('email')}")
                return None
            
            log_test("Auth regression - /auth/me", True, "Returns correct user data")
            return token
    except Exception as e:
        log_test("Auth regression", False, f"Exception: {e}")
        return None


async def test_ai_tools_payment_instructions(token: str):
    """Test 2: Verify GET /api/ai/tools reports get_payment_instructions tool."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BACKEND_URL}/ai/tools",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("AI tools - endpoint", False, f"Status {response.status_code}")
                return
            
            data = response.json()
            
            # Check llm_connected
            if not data.get("llm_connected"):
                log_test("AI tools - LLM connected", False, "llm_connected=false, EMERGENT_LLM_KEY not set")
                return
            
            log_test("AI tools - LLM connected", True, "EMERGENT_LLM_KEY is connected")
            
            # Check for get_payment_instructions tool
            tools = data.get("tools", [])
            payment_tool = None
            for tool in tools:
                # Tools are returned as registry items with name, category, description, implemented
                if tool.get("name") == "get_payment_instructions":
                    payment_tool = tool
                    break
            
            if not payment_tool:
                log_test("AI tools - get_payment_instructions", False, "get_payment_instructions tool not found")
                return
            
            # Verify tool is implemented
            if not payment_tool.get("implemented"):
                log_test("AI tools - get_payment_instructions implemented", False, 
                        "Tool registered but not implemented")
                return
            
            # Verify tool description
            description = payment_tool.get("description", "")
            if "bank" not in description.lower() or "screenshot" not in description.lower():
                log_test("AI tools - get_payment_instructions description", False, 
                        f"Tool description missing key terms: {description}")
                return
            
            log_test("AI tools - get_payment_instructions", True, 
                    f"Tool found and implemented (category: {payment_tool.get('category')}, total {len(tools)} tools)")
    except Exception as e:
        log_test("AI tools - get_payment_instructions", False, f"Exception: {e}")


async def test_gpt54_playground(token: str):
    """Test 3: Verify GPT-5.4 playground still responds."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Test playground - GET session
            response = await client.get(
                f"{BACKEND_URL}/ai/playground/playground-main",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("GPT-5.4 playground - GET session", False, f"Status {response.status_code}")
                return
            
            session_data = response.json()
            if not session_data.get("llm_connected"):
                log_test("GPT-5.4 playground - GET session", False, "llm_connected=false in session")
                return
            
            log_test("GPT-5.4 playground - GET session", True, "Session created with llm_connected=true")
            
            # Test playground - POST with simple question
            response = await client.post(
                f"{BACKEND_URL}/ai/playground/playground-main",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": "What is 3+5?"},
                timeout=60
            )
            if response.status_code != 200:
                log_test("GPT-5.4 playground - POST", False, f"Status {response.status_code}: {response.text[:200]}")
                return
            
            result = response.json()
            reply = result.get("reply", "")
            if not reply or len(reply) < 1:
                log_test("GPT-5.4 playground - POST", False, "No reply from GPT-5.4")
                return
            
            log_test("GPT-5.4 playground - POST", True, f"GPT-5.4 replied: {reply[:100]}")
    except Exception as e:
        log_test("GPT-5.4 playground", False, f"Exception: {e}")


async def test_payment_instructions_tool_execution(token: str):
    """Test 4: Verify get_payment_instructions tool returns placeholder bank details without secrets."""
    try:
        # Load environment variables from backend .env
        import os
        if not os.environ.get("MONGO_URL"):
            with open("/app/backend/.env", "r") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, value = line.strip().split("=", 1)
                        os.environ.setdefault(key, value)
        
        # Import the service to test directly
        sys.path.insert(0, "/app/backend")
        from services.payment_verification import bank_details, customer_payment_instructions
        
        # Test bank_details
        details = bank_details()
        
        # Verify required fields
        required_fields = ["bank_name", "account_title", "account_number", "iban", "is_placeholder"]
        missing_fields = [f for f in required_fields if f not in details]
        if missing_fields:
            log_test("Payment instructions - bank_details fields", False, f"Missing fields: {missing_fields}")
            return
        
        # Verify placeholder flag
        if not details.get("is_placeholder"):
            log_test("Payment instructions - placeholder flag", False, "is_placeholder should be True")
            return
        
        log_test("Payment instructions - bank_details", True, 
                f"Returns: {details['bank_name']}, {details['account_number']}, {details['iban']}")
        
        # Test customer_payment_instructions
        instructions = customer_payment_instructions()
        
        # Verify message contains key information
        message = instructions.get("message", "")
        if not message:
            log_test("Payment instructions - customer message", False, "No message returned")
            return
        
        required_terms = ["bank transfer", "screenshot", details["bank_name"], details["account_number"]]
        missing_terms = [term for term in required_terms if term.lower() not in message.lower()]
        if missing_terms:
            log_test("Payment instructions - customer message content", False, 
                    f"Message missing terms: {missing_terms}")
            return
        
        # Verify no secrets exposed (check for common secret patterns)
        secret_patterns = ["META_APP_SECRET", "EMERGENT_LLM_KEY", "access_token", "Bearer"]
        exposed_secrets = [pattern for pattern in secret_patterns if pattern in message]
        if exposed_secrets:
            log_test("Payment instructions - secret exposure", False, f"Secrets exposed: {exposed_secrets}")
            return
        
        log_test("Payment instructions - customer message", True, 
                "Message contains bank details and screenshot instruction without secrets")
    except Exception as e:
        log_test("Payment instructions - tool execution", False, f"Exception: {e}")


async def test_screenshot_verification_matching(token: str):
    """Test 5: Verify matching amount/account receipt is marked as 'AI marked as paid using customer screenshot'."""
    try:
        # Load environment variables
        if not os.environ.get("MONGO_URL"):
            with open("/app/backend/.env", "r") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, value = line.strip().split("=", 1)
                        os.environ.setdefault(key, value)
        
        # First, create a test order
        sys.path.insert(0, "/app/backend")
        from motor.motor_asyncio import AsyncIOMotorClient
        from bson import ObjectId
        from datetime import datetime, timezone
        
        # Load MongoDB URL
        mongo_url = os.environ.get("MONGO_URL")
        
        db_name = os.environ.get("DB_NAME", "whatsapp_bot")
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Get merchant_id from token
        async with httpx.AsyncClient(timeout=30) as http_client:
            response = await http_client.get(
                f"{BACKEND_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            merchant_id = response.json()["merchant_id"]
        
        # Create a test customer
        customer_result = await db.customers.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "name": "Test Customer Payment",
            "phone": "+923001234567",
            "total_orders": 0,
            "delivered_orders": 0,
            "cancelled_orders": 0,
            "refused_orders": 0,
            "failed_deliveries": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        customer_id = customer_result.inserted_id
        
        # Create a test conversation
        conv_result = await db.conversations.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "customer_id": customer_id,
            "customer_name": "Test Customer Payment",
            "customer_phone": "+923001234567",
            "mode": "AI_ACTIVE",
            "needs_attention": False,
            "last_message": "I want to pay",
            "last_message_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        conversation_id = conv_result.inserted_id
        
        # Create a test order with specific amount
        test_amount = 5000.0
        order_result = await db.shopify_orders.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "customer_id": customer_id,
            "order_number": "TEST-PAY-001",
            "total": test_amount,
            "payment_status": "pending",
            "payment_method": "BANK_TRANSFER",
            "fulfillment_status": "pending",
            "attribution": "ai",
            "items": [{"title": "Test Product", "qty": 1, "price": test_amount}],
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        order_id = order_result.inserted_id
        
        # Create a matching receipt
        receipt_bytes, mime_type = create_synthetic_receipt(
            amount=test_amount,
            account_number="0000000000",
            readable=True
        )
        
        # Test the verification function
        from services.payment_verification import verify_payment_screenshot
        
        result = await verify_payment_screenshot(
            merchant_id=str(merchant_id),
            conversation_id=str(conversation_id),
            image_bytes=receipt_bytes,
            mime_type=mime_type,
            media_id="test_media_123"
        )
        
        # Verify result
        if result.get("decision") != "ai_marked_paid_screenshot":
            log_test("Screenshot verification - matching receipt decision", False, 
                    f"Expected 'ai_marked_paid_screenshot', got '{result.get('decision')}'")
            # Clean up
            await db.customers.delete_one({"_id": customer_id})
            await db.conversations.delete_one({"_id": conversation_id})
            await db.shopify_orders.delete_one({"_id": order_id})
            client.close()
            return
        
        expected_label = "AI marked as paid using customer screenshot"
        if result.get("label") != expected_label:
            log_test("Screenshot verification - matching receipt label", False, 
                    f"Expected '{expected_label}', got '{result.get('label')}'")
            # Clean up
            await db.customers.delete_one({"_id": customer_id})
            await db.conversations.delete_one({"_id": conversation_id})
            await db.shopify_orders.delete_one({"_id": order_id})
            client.close()
            return
        
        log_test("Screenshot verification - matching receipt", True, 
                f"Decision: {result.get('decision')}, Label: {result.get('label')}")
        
        # Verify order was updated
        updated_order = await db.shopify_orders.find_one({"_id": order_id})
        if updated_order.get("payment_verification_status") != "ai_marked_paid_screenshot":
            log_test("Screenshot verification - order update", False, 
                    f"Order not updated correctly: {updated_order.get('payment_verification_status')}")
        else:
            log_test("Screenshot verification - order update", True, 
                    "Order payment_verification_status updated correctly")
        
        # Verify no secrets in result
        result_str = json.dumps(result)
        secret_patterns = ["access_token", "EMERGENT_LLM_KEY", "META_APP_SECRET"]
        exposed = [p for p in secret_patterns if p in result_str]
        if exposed:
            log_test("Screenshot verification - no secrets in response", False, f"Secrets exposed: {exposed}")
        else:
            log_test("Screenshot verification - no secrets in response", True, "No secrets in verification result")
        
        # Clean up
        await db.customers.delete_one({"_id": customer_id})
        await db.conversations.delete_one({"_id": conversation_id})
        await db.shopify_orders.delete_one({"_id": order_id})
        client.close()
    except Exception as e:
        log_test("Screenshot verification - matching receipt", False, f"Exception: {e}")


async def test_screenshot_verification_wrong_amount(token: str):
    """Test 6: Verify wrong amount receipt is marked as 'Payment review required'."""
    try:
        # Load environment variables
        if not os.environ.get("MONGO_URL"):
            with open("/app/backend/.env", "r") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, value = line.strip().split("=", 1)
                        os.environ.setdefault(key, value)
        
        sys.path.insert(0, "/app/backend")
        from motor.motor_asyncio import AsyncIOMotorClient
        from bson import ObjectId
        from datetime import datetime, timezone
        
        # Load MongoDB URL
        mongo_url = os.environ.get("MONGO_URL")
        
        db_name = os.environ.get("DB_NAME", "whatsapp_bot")
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Get merchant_id
        async with httpx.AsyncClient(timeout=30) as http_client:
            response = await http_client.get(
                f"{BACKEND_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            merchant_id = response.json()["merchant_id"]
        
        # Create test data
        customer_result = await db.customers.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "name": "Test Customer Wrong Amount",
            "phone": "+923001234568",
            "total_orders": 0,
            "delivered_orders": 0,
            "cancelled_orders": 0,
            "refused_orders": 0,
            "failed_deliveries": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        customer_id = customer_result.inserted_id
        
        conv_result = await db.conversations.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "customer_id": customer_id,
            "customer_name": "Test Customer Wrong Amount",
            "customer_phone": "+923001234568",
            "mode": "AI_ACTIVE",
            "needs_attention": False,
            "last_message": "Payment sent",
            "last_message_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        conversation_id = conv_result.inserted_id
        
        # Create order with amount 5000
        expected_amount = 5000.0
        order_result = await db.shopify_orders.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "customer_id": customer_id,
            "order_number": "TEST-PAY-002",
            "total": expected_amount,
            "payment_status": "pending",
            "payment_method": "BANK_TRANSFER",
            "fulfillment_status": "pending",
            "attribution": "ai",
            "items": [{"title": "Test Product", "qty": 1, "price": expected_amount}],
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        order_id = order_result.inserted_id
        
        # Create receipt with WRONG amount (3000 instead of 5000)
        wrong_amount = 3000.0
        receipt_bytes, mime_type = create_synthetic_receipt(
            amount=wrong_amount,
            account_number="0000000000",
            readable=True
        )
        
        # Test verification
        from services.payment_verification import verify_payment_screenshot
        
        result = await verify_payment_screenshot(
            merchant_id=str(merchant_id),
            conversation_id=str(conversation_id),
            image_bytes=receipt_bytes,
            mime_type=mime_type,
            media_id="test_media_124"
        )
        
        # Verify result
        if result.get("decision") != "review_required":
            log_test("Screenshot verification - wrong amount decision", False, 
                    f"Expected 'review_required', got '{result.get('decision')}'")
        else:
            log_test("Screenshot verification - wrong amount decision", True, 
                    f"Correctly marked as review_required for amount mismatch")
        
        expected_label = "Payment review required"
        if result.get("label") != expected_label:
            log_test("Screenshot verification - wrong amount label", False, 
                    f"Expected '{expected_label}', got '{result.get('label')}'")
        else:
            log_test("Screenshot verification - wrong amount label", True, 
                    f"Label correctly set to '{expected_label}'")
        
        # Verify conversation needs_attention is set
        updated_conv = await db.conversations.find_one({"_id": conversation_id})
        if not updated_conv.get("needs_attention"):
            log_test("Screenshot verification - seller attention flag", False, 
                    "needs_attention not set on conversation")
        else:
            log_test("Screenshot verification - seller attention flag", True, 
                    "Conversation flagged for seller attention")
        
        # Clean up
        await db.customers.delete_one({"_id": customer_id})
        await db.conversations.delete_one({"_id": conversation_id})
        await db.shopify_orders.delete_one({"_id": order_id})
        client.close()
    except Exception as e:
        log_test("Screenshot verification - wrong amount", False, f"Exception: {e}")


async def test_screenshot_verification_unreadable(token: str):
    """Test 7: Verify unreadable receipt is marked as 'Payment review required'."""
    try:
        # Load environment variables
        if not os.environ.get("MONGO_URL"):
            with open("/app/backend/.env", "r") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, value = line.strip().split("=", 1)
                        os.environ.setdefault(key, value)
        
        sys.path.insert(0, "/app/backend")
        from motor.motor_asyncio import AsyncIOMotorClient
        from bson import ObjectId
        from datetime import datetime, timezone
        
        # Load MongoDB URL
        mongo_url = os.environ.get("MONGO_URL")
        
        db_name = os.environ.get("DB_NAME", "whatsapp_bot")
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Get merchant_id
        async with httpx.AsyncClient(timeout=30) as http_client:
            response = await http_client.get(
                f"{BACKEND_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            merchant_id = response.json()["merchant_id"]
        
        # Create test data
        customer_result = await db.customers.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "name": "Test Customer Unreadable",
            "phone": "+923001234569",
            "total_orders": 0,
            "delivered_orders": 0,
            "cancelled_orders": 0,
            "refused_orders": 0,
            "failed_deliveries": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        customer_id = customer_result.inserted_id
        
        conv_result = await db.conversations.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "customer_id": customer_id,
            "customer_name": "Test Customer Unreadable",
            "customer_phone": "+923001234569",
            "mode": "AI_ACTIVE",
            "needs_attention": False,
            "last_message": "Here is receipt",
            "last_message_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        conversation_id = conv_result.inserted_id
        
        order_result = await db.shopify_orders.insert_one({
            "merchant_id": ObjectId(merchant_id),
            "customer_id": customer_id,
            "order_number": "TEST-PAY-003",
            "total": 5000.0,
            "payment_status": "pending",
            "payment_method": "BANK_TRANSFER",
            "fulfillment_status": "pending",
            "attribution": "ai",
            "items": [{"title": "Test Product", "qty": 1, "price": 5000.0}],
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        order_id = order_result.inserted_id
        
        # Create UNREADABLE receipt
        receipt_bytes, mime_type = create_synthetic_receipt(
            amount=5000.0,
            account_number="0000000000",
            readable=False  # Unreadable
        )
        
        # Test verification
        from services.payment_verification import verify_payment_screenshot
        
        result = await verify_payment_screenshot(
            merchant_id=str(merchant_id),
            conversation_id=str(conversation_id),
            image_bytes=receipt_bytes,
            mime_type=mime_type,
            media_id="test_media_125"
        )
        
        # Verify result
        if result.get("decision") != "review_required":
            log_test("Screenshot verification - unreadable receipt", False, 
                    f"Expected 'review_required', got '{result.get('decision')}'")
        else:
            log_test("Screenshot verification - unreadable receipt", True, 
                    "Correctly marked as review_required for unreadable receipt")
        
        # Clean up
        await db.customers.delete_one({"_id": customer_id})
        await db.conversations.delete_one({"_id": conversation_id})
        await db.shopify_orders.delete_one({"_id": order_id})
        client.close()
    except Exception as e:
        log_test("Screenshot verification - unreadable", False, f"Exception: {e}")


async def test_orders_endpoint_verification_label(token: str):
    """Test 8: Verify Orders endpoint includes payment_verification_label."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BACKEND_URL}/orders",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                log_test("Orders endpoint - access", False, f"Status {response.status_code}")
                return
            
            orders = response.json()
            
            # Check if any orders exist with payment verification fields
            # (from previous tests or existing data)
            has_verification_fields = False
            for order in orders:
                if "payment_verification_label" in order or "payment_verification_status" in order:
                    has_verification_fields = True
                    
                    # Verify no secrets in order data
                    order_str = json.dumps(order)
                    secret_patterns = ["access_token", "EMERGENT_LLM_KEY", "META_APP_SECRET", "screenshot_bytes"]
                    exposed = [p for p in secret_patterns if p in order_str]
                    if exposed:
                        log_test("Orders endpoint - no secrets", False, f"Secrets exposed: {exposed}")
                        return
                    
                    # Verify screenshot path is tenant-scoped (should contain merchant_id)
                    if "payment_screenshot_path" in order:
                        path = order["payment_screenshot_path"]
                        if not path or "/uploads/payment_screenshots/" not in path:
                            log_test("Orders endpoint - tenant-scoped paths", False, 
                                    f"Screenshot path not tenant-scoped: {path}")
                            return
                    
                    break
            
            if has_verification_fields:
                log_test("Orders endpoint - verification fields", True, 
                        "Orders include payment_verification_label and no secrets exposed")
            else:
                log_test("Orders endpoint - verification fields", True, 
                        "Orders endpoint accessible (no orders with verification yet)")
    except Exception as e:
        log_test("Orders endpoint - verification label", False, f"Exception: {e}")


async def test_tenant_isolation(token: str):
    """Test 9: Verify payment fields and screenshot paths are tenant-scoped."""
    try:
        sys.path.insert(0, "/app/backend")
        from pathlib import Path
        
        # Check that upload directory structure is tenant-scoped
        upload_root = Path("/app/backend/uploads/payment_screenshots")
        
        if upload_root.exists():
            # List directories (should be merchant IDs)
            merchant_dirs = [d for d in upload_root.iterdir() if d.is_dir()]
            
            # Verify directory names look like ObjectIds (24 hex chars)
            for merchant_dir in merchant_dirs:
                dir_name = merchant_dir.name
                if len(dir_name) != 24 or not all(c in '0123456789abcdef' for c in dir_name):
                    log_test("Tenant isolation - screenshot paths", False, 
                            f"Non-tenant-scoped directory found: {dir_name}")
                    return
            
            log_test("Tenant isolation - screenshot paths", True, 
                    f"Screenshot storage is tenant-scoped ({len(merchant_dirs)} merchant directories)")
        else:
            log_test("Tenant isolation - screenshot paths", True, 
                    "Upload directory not yet created (no screenshots uploaded)")
    except Exception as e:
        log_test("Tenant isolation", False, f"Exception: {e}")


async def main():
    """Run all payment screenshot verification tests."""
    print("=" * 80)
    print("Payment Screenshot Verification Testing")
    print("=" * 80)
    print()
    
    # Test 1: Auth regression
    token = await test_auth_regression()
    if not token:
        print("\n❌ Auth failed - cannot continue with authenticated tests")
        print_summary()
        return
    print()
    
    # Test 2: AI tools includes get_payment_instructions
    await test_ai_tools_payment_instructions(token)
    print()
    
    # Test 3: GPT-5.4 playground still works
    await test_gpt54_playground(token)
    print()
    
    # Test 4: Payment instructions tool execution
    await test_payment_instructions_tool_execution(token)
    print()
    
    # Test 5: Screenshot verification - matching receipt
    await test_screenshot_verification_matching(token)
    print()
    
    # Test 6: Screenshot verification - wrong amount
    await test_screenshot_verification_wrong_amount(token)
    print()
    
    # Test 7: Screenshot verification - unreadable
    await test_screenshot_verification_unreadable(token)
    print()
    
    # Test 8: Orders endpoint includes verification label
    await test_orders_endpoint_verification_label(token)
    print()
    
    # Test 9: Tenant isolation
    await test_tenant_isolation(token)
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

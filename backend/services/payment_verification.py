"""Bank-transfer instructions and conservative payment screenshot verification."""
import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId

from database import db
from audit import log_action

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
UPLOAD_ROOT = Path("/app/backend/uploads/payment_screenshots")


def bank_details() -> dict:
    return {
        "bank_name": os.environ.get("PAYMENT_BANK_NAME", "Chatify AI Demo Bank"),
        "account_title": os.environ.get("PAYMENT_ACCOUNT_TITLE", "Chatify AI Demo Store"),
        "account_number": os.environ.get("PAYMENT_ACCOUNT_NUMBER", "0000000000"),
        "iban": os.environ.get("PAYMENT_IBAN", "PK00CHAT0000000000000000"),
        "is_placeholder": True,
    }


def customer_payment_instructions() -> dict:
    details = bank_details()
    return {
        **details,
        "message": (
            "You can pay by bank transfer. This is a demo placeholder account — do not send real funds.\n"
            f"Bank: {details['bank_name']}\n"
            f"Account title: {details['account_title']}\n"
            f"Account number: {details['account_number']}\n"
            f"IBAN: {details['iban']}\n\n"
            "After transferring, please send a clear screenshot of the payment receipt here."
        ),
    }


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


async def _analyze_image(image_bytes: bytes, mime_type: str, expected: dict) -> dict:
    if not os.environ.get("OPENAI_API_KEY"):
        return {"decision": "review_required", "confidence": 0, "reasons": ["AI verification is not configured."]}
    from emergentintegrations.llm.chat import ImageContent, LlmChat, StreamDone, TextDelta, UserMessage

    prompt = f"""You are a payment receipt verification assistant. Analyze the attached customer screenshot.
Return ONLY valid JSON with these keys: receipt_visible (boolean), paid (boolean), amount_pkr (number or null),
reference (string or null), transaction_date (string or null), recipient_account (string or null),
recipient_matches_expected (boolean), confidence (number from 0 to 1), reasons (array of short strings).
Do not infer missing values. Treat unreadable, cropped, edited, or ambiguous evidence as false/low confidence.
Expected order amount in PKR: {expected.get('total')}
Expected bank: {expected.get('bank_name')}
Expected account title: {expected.get('account_title')}
Expected account number or IBAN: {expected.get('account_number')} / {expected.get('iban')}
"""
    chat = (LlmChat(api_key=os.environ["OPENAI_API_KEY"], session_id=f"payment-proof-{expected['order_id']}-{datetime.now(timezone.utc).timestamp()}", system_message="Verify payment screenshots conservatively; never claim payment is confirmed without visible evidence.")
            .with_model("openai", "gpt-5.4"))
    chunks = []
    async for event in chat.stream_message(UserMessage(text=prompt, file_contents=[ImageContent(image_base64=base64.b64encode(image_bytes).decode("ascii"))])):
        if isinstance(event, TextDelta):
            chunks.append(event.content)
        elif isinstance(event, StreamDone):
            break
    return _extract_json("".join(chunks))


async def verify_payment_screenshot(merchant_id: str, conversation_id: str, image_bytes: bytes, mime_type: str, media_id: str | None = None) -> dict:
    """Persist a tenant-scoped receipt and conservatively update the customer's latest order."""
    oid = ObjectId(merchant_id)
    if mime_type not in ALLOWED_MIME:
        return {"decision": "review_required", "customer_message": "Thanks, please send a clear JPG, PNG, or WEBP payment screenshot so our team can review it."}
    if len(image_bytes) > 10 * 1024 * 1024:
        return {"decision": "review_required", "customer_message": "The payment screenshot is too large. Please send a smaller clear screenshot."}
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "merchant_id": oid})
    if not conversation:
        return {"decision": "review_required", "customer_message": "Thanks, our team will review the payment proof."}
    order_query = {"merchant_id": oid, "payment_status": {"$ne": "paid"}}
    if conversation.get("customer_id"):
        order_query["customer_id"] = conversation["customer_id"]
    else:
        order_query["conversation_id"] = conversation["_id"]
    order = await db.shopify_orders.find_one(order_query, sort=[("created_at", -1)])
    UPLOAD_ROOT.joinpath(str(oid)).mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.bin"
    path = UPLOAD_ROOT / str(oid) / filename
    with path.open("wb") as handle:
        for start in range(0, len(image_bytes), 1024 * 1024):
            handle.write(image_bytes[start:start + 1024 * 1024])
    if not order:
        await db.conversations.update_one({"_id": conversation["_id"], "merchant_id": oid}, {"$set": {"needs_attention": True, "payment_review_status": "review_required"}})
        return {"decision": "review_required", "customer_message": "Thanks, we received the payment screenshot. Our team will review it."}
    expected = {"order_id": str(order["_id"]), "total": order.get("total", 0), **bank_details()}
    result = await _analyze_image(image_bytes, mime_type, expected)
    confidence = float(result.get("confidence") or 0)
    amount = result.get("amount_pkr")
    amount_matches = isinstance(amount, (int, float)) and abs(float(amount) - float(expected["total"])) <= 1
    account_matches = bool(result.get("recipient_matches_expected"))
    ai_recommended = bool(result.get("receipt_visible") and result.get("paid") and confidence >= 0.9 and amount_matches and account_matches)
    # AI recommends; the seller remains the final payment approver.
    decision = "review_required"
    now = datetime.now(timezone.utc).isoformat()
    update = {
        "payment_verification_status": decision,
        "payment_verification_label": "Payment review required",
        "payment_ai_recommendation": "approve" if ai_recommended else "review",
        "payment_verification_confidence": confidence,
        "payment_verification_reasons": result.get("reasons", []),
        "payment_screenshot_path": str(path),
        "payment_screenshot_media_id": media_id,
        "payment_screenshot_mime_type": mime_type,
        "payment_verified_at": now,
        "payment_reference": result.get("reference"),
        "payment_extracted_amount": amount,
        "payment_transaction_date": result.get("transaction_date"),
        "payment_recipient_account": result.get("recipient_account"),
    }
    await db.conversations.update_one(
        {"_id": conversation["_id"], "merchant_id": oid},
        {"$set": {"needs_attention": True, "payment_review_status": "review_required"}},
    )
    await db.shopify_orders.update_one({"_id": order["_id"], "merchant_id": oid}, {"$set": update})
    await log_action(merchant_id, "payment_screenshot_verified", actor="ai", resource_id=str(order["_id"]),
                     detail={"decision": decision, "ai_recommendation": update["payment_ai_recommendation"],
                             "confidence": confidence, "amount_matches": amount_matches, "account_matches": account_matches})
    return {"decision": decision, "label": update["payment_verification_label"],
            "customer_message": "Thanks, we received your payment screenshot. Our team will review the payment proof shortly."}

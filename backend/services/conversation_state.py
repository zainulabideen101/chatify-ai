"""Deterministic latest-turn intent and state extraction for WhatsApp conversations."""
import re
from copy import deepcopy
from bson import ObjectId

from database import db

INTENTS = {
    "greeting_or_small_talk",
    "product_availability",
    "new_order",
    "order_tracking",
    "delivery_estimate",
    "order_modification",
    "cancellation",
    "confirmation_yes",
    "confirmation_no",
    "unclear_request",
}

ORDER_NUMBER_PATTERN = re.compile(r"\bCFY-[A-Z0-9]{8}\b", re.IGNORECASE)
YES_PATTERN = re.compile(r"^(yes|yeah|yep|ok|okay|haan|han|jee|ji|bilkul|theek|theek hai|kar dein|kar do|order kar do|new order chahiye|ہاں|جی|بالکل|ٹھیک ہے)$", re.IGNORECASE)
NO_PATTERN = re.compile(r"^(no|nahi|nahin|naheen|cancel|nahi chahiye|nahin chahiye|نہیں|نہيں|منسوخ)$", re.IGNORECASE)
GREETING_PATTERN = re.compile(r"^(hi|hello|hey|salam|salaam|aoa|assalam o alaikum|السلام علیکم)[!,.\s]*$", re.IGNORECASE)
AVAILABILITY_PATTERN = re.compile(r"\b(available|availability|stock|in stock|milta|milti|milega|hai available|ہے|دستیاب)\b", re.IGNORECASE)
CANCELLATION_PATTERN = re.compile(r"\b(cancel|cancellation|cancelled|cancel kar|cancel kr|order nahi chahiye|order band|منسوخ)\b", re.IGNORECASE)
NEW_ORDER_PATTERN = re.compile(r"\b(new order|separate order|additional order|order kar|order kr|order place|place an order|want to order|wants? to buy|buy|purchase|lena|leni|mangwana|mangwani|chahiye|krna hai|karni hai|krdo|kardo|krdein|karein)\b", re.IGNORECASE)
TRACKING_PATTERN = re.compile(r"\b(track|tracking|status|where is my order|old order|purana order|mera order kahan|kahan pohanch|kahan pohnch|pohanch gaya|pohnch gaya|where.*order)\b", re.IGNORECASE)
DELIVERY_PATTERN = re.compile(r"\b(eta|deliver|delivery|kab ayega|kab aayega|kab tak|kitne din|when.*arrive|when.*deliver|aayega|ayega)\b", re.IGNORECASE)
MODIFICATION_PATTERN = re.compile(r"\b(change|modify|update|replace|edit|size change|address change|change kar|badal|tabdeel|add .*order)\b", re.IGNORECASE)
SIZE_PATTERN = re.compile(r"\b(xs|small|medium|large|xl|xxl|2xl|3xl|chhota|choti|bara|bari)\b", re.IGNORECASE)
QUANTITY_PATTERN = re.compile(r"\b(\d+)\s*(?:piece|pieces|pcs|items?|orders?)?\b", re.IGNORECASE)
ADDRESS_PATTERN = re.compile(r"(?:house|h\.?\s*no|street|st\.?|road|block|sector|town|colony|lahore|karachi|islamabad|rawalpindi|peshawar|multan|address|پتہ)", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _base_state(previous: dict | None, customer_id: str | None) -> dict:
    state = {
        "conversation_id": None,
        "customer_id": customer_id,
        "intent": "unclear_request",
        "product": None,
        "variant": None,
        "size": None,
        "quantity": None,
        "delivery_address": None,
        "order_number": None,
        "order_number_candidate": None,
        "pending_confirmation": False,
        "confirmation_type": None,
        "duplicate_check_required": False,
        "duplicate_check_completed": False,
        "last_user_message": None,
        "last_detected_intent": None,
        "flow_intent": None,
        "state_version": 0,
    }
    if previous:
        state.update(deepcopy(previous))
    if customer_id:
        state["customer_id"] = customer_id
    return state


async def detect_latest_turn(merchant_id: str, conversation_id: str, customer_id: str | None, text: str, previous: dict | None = None) -> dict:
    """Extract the latest turn without allowing stale conversation context to choose intent."""
    message = _clean(text)
    lowered = message.casefold()
    previous_state = _base_state(previous, customer_id)
    order_match = ORDER_NUMBER_PATTERN.search(message)
    order_number = order_match.group(0).upper() if order_match else None

    products = await db.products.find({"merchant_id": ObjectId(merchant_id)}, {"title": 1}).limit(200).to_list(200)
    product = next(
        (doc.get("title") for doc in sorted(products, key=lambda doc: len(doc.get("title", "")), reverse=True)
         if doc.get("title") and doc["title"].casefold() in lowered),
        None,
    )
    size_match = SIZE_PATTERN.search(message)
    size = size_match.group(1).title() if size_match else None
    quantity_match = QUANTITY_PATTERN.search(message)
    quantity = int(quantity_match.group(1)) if quantity_match else None
    address = message if ADDRESS_PATTERN.search(message) and (NEW_ORDER_PATTERN.search(message) or previous_state.get("intent") == "new_order") else None

    positive = bool(YES_PATTERN.fullmatch(re.sub(r"[.!?,]+$", "", message).strip()))
    negative = bool(NO_PATTERN.fullmatch(re.sub(r"[.!?,]+$", "", message).strip()))
    clearly_new = bool(NEW_ORDER_PATTERN.search(message))
    has_product_change = bool(product or size or quantity or address)

    if CANCELLATION_PATTERN.search(message):
        intent = "cancellation"
    elif order_number and not clearly_new:
        intent = "delivery_estimate" if DELIVERY_PATTERN.search(message) else "order_tracking"
    elif AVAILABILITY_PATTERN.search(message):
        intent = "product_availability"
    elif clearly_new or (size and previous_state.get("product")):
        intent = "new_order"
    elif DELIVERY_PATTERN.search(message) or TRACKING_PATTERN.search(message):
        intent = "delivery_estimate" if DELIVERY_PATTERN.search(message) else "order_tracking"
    elif MODIFICATION_PATTERN.search(message):
        intent = "order_modification"
    elif previous_state.get("pending_confirmation") and positive:
        intent = "confirmation_yes"
    elif previous_state.get("pending_confirmation") and negative:
        intent = "confirmation_no"
    elif GREETING_PATTERN.fullmatch(message):
        intent = "greeting_or_small_talk"
    elif positive or negative:
        intent = "confirmation_yes" if positive else "confirmation_no"
    else:
        intent = "unclear_request"

    state = _base_state(previous, customer_id)
    previous_product = state.get("product")
    previous_size = state.get("size")
    previous_address = state.get("delivery_address")
    product_changed = bool(product and previous_product and product.casefold() != str(previous_product).casefold())
    size_changed = bool(size and previous_size and size.casefold() != str(previous_size).casefold())
    address_changed = bool(address and previous_address and _clean(address).casefold() != _clean(str(previous_address)).casefold())
    stale_new_order_restart = bool(intent == "new_order" and address and not state.get("pending_confirmation"))
    flow_context_changed = product_changed or size_changed or address_changed or stale_new_order_restart
    if product:
        state["product"] = product
    if size:
        state["size"] = size
    if quantity:
        state["quantity"] = quantity
    if address:
        state["delivery_address"] = address

    if intent in {"new_order", "product_availability"} and flow_context_changed:
        state.update({
            "order_number": None,
            "order_number_candidate": None,
            "variant": None,
            "duplicate_check_required": False,
            "duplicate_check_completed": False,
            "pending_confirmation": False,
            "confirmation_type": None,
            "flow_intent": "new_order" if intent == "new_order" else None,
        })
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id), "merchant_id": ObjectId(merchant_id)},
            {"$unset": {"pending_order": ""}},
        )
    elif intent in {"greeting_or_small_talk", "product_availability", "order_tracking", "delivery_estimate", "cancellation", "order_modification", "unclear_request"}:
        state.update({
            "pending_confirmation": False,
            "confirmation_type": None,
            "duplicate_check_required": False,
            "duplicate_check_completed": False,
        })
        if intent != "order_tracking" and intent != "delivery_estimate":
            state["order_number_candidate"] = None

    if order_number:
        state["order_number_candidate"] = order_number
    if intent == "confirmation_yes":
        state["flow_intent"] = state.get("flow_intent") or "new_order"
    elif intent == "confirmation_no":
        state["flow_intent"] = None
        state["pending_confirmation"] = False
    elif intent == "new_order":
        state["flow_intent"] = "new_order"

    state.update({
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "intent": intent,
        "last_user_message": message,
        "last_detected_intent": intent,
        "state_version": int(state.get("state_version") or 0) + 1,
    })
    return state

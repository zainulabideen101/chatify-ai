"""AI Agent service: hybrid orchestrator using GPT-5.4 (Emergent Universal Key).

Design principle: "AI decides what the customer means. Backend decides what the
system is allowed to do." The LLM requests a tool; `services.ai_tools.dispatch`
validates + executes it against tenant data. The LLM never touches external APIs.
"""
import os
import re
import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
from bson import ObjectId

from database import db
from models import iso_now
from services.ai_tools import TOOL_SCHEMAS, dispatch


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ToolSpec:
    name: str
    category: str
    description: str
    side_effect: bool = False
    tenant_aware: bool = True


class ToolRegistry:
    """Catalog of controlled tools the LLM may request. Every tool is tenant-aware."""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._register_defaults()

    def register(self, spec: ToolSpec):
        self._tools[spec.name] = spec

    def list(self) -> list[dict]:
        live = {s["function"]["name"] for s in TOOL_SCHEMAS}
        return [{"name": t.name, "category": t.category, "description": t.description,
                 "side_effect": t.side_effect, "tenant_aware": t.tenant_aware,
                 "implemented": t.name in live} for t in self._tools.values()]

    def _register_defaults(self):
        defs = [
            ("search_products", "product", "Search catalog by query/attributes", False),
            ("get_product", "product", "Fetch a product by id", False),
            ("get_product_variant", "product", "Fetch a variant by id", False),
            ("check_variant_availability", "product", "Check current inventory/price for a variant", False),
            ("check_inventory", "product", "Check current inventory for a variant", False),
            ("get_product_price", "product", "Get current price for a variant", False),
            ("get_collection_products", "product", "List products in a collection", False),
            ("recommend_related", "product", "Get cross-sell/upsell related products", False),
            ("find_customer", "customer", "Find a customer by phone/name", False),
            ("create_customer", "customer", "Create a customer record", True),
            ("update_customer", "customer", "Update a customer record", True),
            ("get_customer_orders", "customer", "List a customer's orders", False),
            ("get_customer_preferences", "customer", "Get stored customer memory", False),
            ("find_product_media", "media", "Find media for a product by tag/type", False),
            ("send_image", "media", "Send an image over WhatsApp", True),
            ("send_video", "media", "Send a video over WhatsApp", True),
            ("send_product_media", "media", "Send appropriate product media", True),
            ("create_order_session", "order", "Start an order session", True),
            ("add_item_to_order", "order", "Add an item to the order session", True),
            ("remove_item_from_order", "order", "Remove an item", True),
            ("change_quantity", "order", "Change item quantity", True),
            ("change_variant", "order", "Change item variant", True),
            ("build_order_summary", "order", "Compute an order summary", False),
            ("calculate_order_total", "order", "Compute order total", False),
            ("validate_order", "order", "Validate order before creation", False),
            ("create_draft_order", "order", "Create a Shopify draft order", True),
            ("create_shopify_order", "order", "Create the final Shopify order", True),
            ("validate_address", "address", "Validate deliverability of an address", False),
            ("detect_missing_address_information", "address", "Detect missing address parts", False),
            ("get_shipping_estimate", "shipping", "Get delivery cost and days for a city", False),
            ("get_shipping_options", "shipping", "Get available shipping options", False),
            ("calculate_shipping_cost", "shipping", "Calculate shipping cost", False),
            ("search_knowledge_base", "knowledge", "Retrieve merchant policy/FAQ text (RAG)", False),
            ("send_message", "whatsapp", "Send a WhatsApp text message", True),
            ("send_template", "whatsapp", "Send an approved WhatsApp template", True),
            ("get_payment_instructions", "payment", "Provide bank-transfer details and request a payment screenshot", False),
            ("request_human_attention", "escalation", "Flag conversation for a human", True),
            ("pause_ai", "escalation", "Pause AI auto-responses", True),
            ("resume_ai", "escalation", "Resume AI auto-responses", True),
            ("record_ai_action", "analytics", "Record an AI action event", True),
            ("record_recommendation", "analytics", "Record a recommendation event", True),
            ("record_conversion_event", "analytics", "Record a conversion event", True),
        ]
        for name, cat, desc, side in defs:
            self.register(ToolSpec(name=name, category=cat, description=desc, side_effect=side))


class LLMNotConfigured(Exception):
    pass


SALES_MODE_GUIDANCE = {
    "SUPPORT_ONLY": "Focus on answering questions and helping. Do NOT push recommendations or upsells.",
    "HELPFUL_SALES": "Be helpful and suggest relevant products/cross-sells when genuinely useful. Do not be pushy.",
    "AGGRESSIVE_SALES": "Proactively recommend, cross-sell and upsell on every reasonable opportunity, while staying honest.",
}


_EMOJI_CATEGORIES = {"So", "Sk"}  # Other-symbol + Modifier-symbol (covers emoji & pictographs)


def sanitize_reply(text: str) -> str:
    """Enforce persona rules at the output layer: strip emojis, markdown, and stray asterisks/underscores
    that the LLM may still emit. Keeps regular punctuation and multilingual letters intact."""
    if not text:
        return text
    # Remove emojis and pictographs (Unicode symbol categories used by emoji).
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) not in _EMOJI_CATEGORIES)
    # Neutralise any stray message-split marker so we always emit ONE WhatsApp message.
    cleaned = cleaned.replace("[[MESSAGE_BREAK]]", "\n")
    # Strip markdown emphasis markers (*, _, `) used for bold/italic/code without breaking numeric/URL content.
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"`+", "", cleaned)
    # Only remove underscores that look like markdown emphasis (surrounded by word chars), preserve those inside URLs/IDs.
    cleaned = re.sub(r"(?<!\w)_(?=\S)|(?<=\S)_(?!\w)", "", cleaned)
    # Collapse whitespace introduced by removals.
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()



_SALES_REFUSAL_PATTERN = re.compile(
    r"\b(no thanks|not interested|no need|don't need|do not need|don't want|do not want|that's all|bas itna|bas itni|nahi chahiye|nahin chahiye|nahi lena|nahin lena)\b",
    re.IGNORECASE,
)


async def note_sales_refusal(merchant_id: str, conversation_id: str, customer_message: str) -> bool:
    """Persist a customer's refusal so future turns cannot re-trigger recommendations."""
    if not _SALES_REFUSAL_PATTERN.search(customer_message or ""):
        return False
    oid = ObjectId(merchant_id)
    previous = await db.messages.find_one(
        {"conversation_id": ObjectId(conversation_id), "merchant_id": oid, "direction": "outbound"},
        sort=[("created_at", -1)],
    )
    if not previous:
        return False
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id), "merchant_id": oid},
        {"$set": {"sales_opt_out": True, "sales_opt_out_at": iso_now()}},
    )
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "merchant_id": oid})
    if conversation and conversation.get("customer_id"):
        await db.customers.update_one(
            {"_id": conversation["customer_id"], "merchant_id": oid},
            {"$set": {"sales_opt_out": True, "sales_opt_out_at": iso_now()}},
        )
    return True


class AIAgentService:
    def __init__(self):
        self.tools = ToolRegistry()

    @property
    def llm_connected(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    async def _build_system_prompt(self, merchant_id: str) -> str:
        oid = ObjectId(merchant_id)
        merchant = await db.merchants.find_one({"_id": oid})
        settings = await db.ai_settings.find_one({"merchant_id": oid}) or {}
        rules = await db.business_rules.find({"merchant_id": oid, "active": True}).to_list(50)

        store = merchant.get("store_name", "the store") if merchant else "the store"
        mode = settings.get("sales_mode", "HELPFUL_SALES")
        rule_lines = "\n".join(f"- {r['instruction']}" for r in rules) or "- (none)"
        toggles = []
        if not settings.get("cross_selling", True): toggles.append("Cross-selling is DISABLED.")
        if not settings.get("upselling", True): toggles.append("Upselling is DISABLED.")
        if not settings.get("media_sending", True): toggles.append("Do not offer to send media.")
        toggle_text = "\n".join(f"- {t}" for t in toggles) or "- (all sales features enabled per mode)"

        return f"""You are the AI sales and customer-service assistant for "{store}", a Pakistani e-commerce store selling on WhatsApp. You act like a real, professional human sales employee — never like a bot.

PERSONA AND TONE (STRICT — NEVER BREAK):
- Always stay polite, formal, and professional. Address customers respectfully (e.g., "Sir", "Ma'am", "Ap" in Urdu).
- NEVER use emojis of any kind (no faces, hearts, hands, stars, checkmarks — nothing). Plain text only.
- NEVER use markdown formatting. No asterisks (*), no underscores (_), no backticks, no bullet dashes, no bold/italic markers. Write in plain sentences.
- ALWAYS send exactly ONE WhatsApp message per turn. Keep it concise (usually 1-4 short lines). Do NOT split your reply into multiple messages. Do NOT emit any message-split marker.
- Ask ONE question at a time when a question is needed.
- If a customer is rude, abusive, uses profanity, or insults you, DO NOT match their tone, DO NOT apologise for their behaviour, and DO NOT get defensive. Stay calm and courteous. Respond briefly and professionally. If abuse continues or the customer demands a human, call request_human_attention immediately.
- Never argue with the customer. Never make jokes. Never use slang like "bro", "yaar", "bhai" unless the customer used it first and even then keep it minimal and respectful.

LANGUAGE: Reply in the SAME language/style the customer uses — Roman Urdu, Urdu, English, or mixed. If they write Roman Urdu (e.g. "black hoodie medium mein hai?"), reply in natural Roman Urdu. Keep it formal in that language.

SALES MODE = {mode}: {SALES_MODE_GUIDANCE.get(mode, "")}
{toggle_text}

HOW TO HANDLE EACH TURN — FOLLOW THIS DECISION FLOW EVERY TIME:
1. Read the CUSTOMER'S LATEST MESSAGE carefully. What are they specifically asking for RIGHT NOW? Do not assume — read the exact words.
2. Look at the RECENT ORDERS section below (if present). Those orders already exist for this customer. Never re-create an order that is already listed there for the same product+variant.
3. Classify the customer's intent and act accordingly:
   a. NEW ORDER for a product NOT in Recent Orders: gather product, variant, quantity, delivery details, payment method. Use check_variant_availability. Use build_order_summary and ask for explicit confirmation. Only AFTER the customer confirms with a clear "yes / ok / confirm / theek hai / haan", call create_shopify_order.
   b. NEW ORDER for the SAME product+variant already in Recent Orders: ask a clarifying question — is this an additional quantity, or are they referring to the existing order? Do NOT auto-create.
   c. ADDING to an existing order (e.g. "aur ek cap bhi add kar do"): confirm the addition politely and either update via create_shopify_order for the added items as a new order OR ask them to confirm you should create a separate order for the added items.
   d. PAYMENT DETAILS request ("payment details bhejo", "online pay", "bank details"): call get_payment_instructions and share only the payment details. Do not repeat order confirmation.
   e. TRACKING / ORDER STATUS / "kab aayega": answer using the order info in Recent Orders. Do NOT create a new order.
   f. CANCELLATION: acknowledge and let the cancellation flow handle it. Do NOT create a new order.
   g. GREETING / SMALL TALK ("hello", "salaam", "hi"): reply with a short courteous greeting and ask how you can help. Do NOT restate previous orders unless asked.
   h. PRODUCT INFO / AVAILABILITY / PRICE / MEDIA request: use the relevant tools (search_products, check_variant_availability, find_product_media, search_knowledge_base). Do NOT create an order.
   i. COMPLAINT / REFUND / RETURN / ABUSE / EXPLICIT HUMAN REQUEST: call request_human_attention with the reason.
4. NEVER copy your own previous reply. If your last message said "order place ho gaya", the NEXT message must NOT start the same way — it must answer the customer's newest question. Repetition is the biggest failure mode; avoid it at all costs.

HARD RULES (never break):
- NEVER invent inventory, prices, discounts, product specs, shipping times, or policies. ALWAYS call a tool to get real data first.
- Use check_variant_availability before confirming any product is available or stating a price.
- Use find_product_media when the customer asks for a photo/pic/video. Only claim a media is a "real photo" if its tags say so.
- Use search_knowledge_base before answering policy questions (returns, shipping, warranty, sizes).
- Use validate_address before accepting a delivery address; if incomplete, ask for exactly the missing parts.
- Before any order, use build_order_summary and show the customer the summary, then ask for explicit confirmation. After explicit confirmation, call create_shopify_order with the confirmed items/address/payment method. This tool always creates the confirmed order in Chatify AI first. Only say the order is placed when the tool returns created=true. If the tool returns created=false with duplicate=true, tell the customer politely that the same order already exists and refer to its order number; ask if they want an ADDITIONAL separate order and wait for a clear yes. If created=false for any other reason, explain only that the order details need clarification; never mention Shopify, connections, catalog sync, integrations, or technical system details.
- Never tell customers what platform integrations are connected, disconnected, synced, or unavailable. Those are internal merchant details. When an order is created locally, speak about it exactly like any other placed order.
- If you are unsure, the request is a complaint/refund, the customer is abusive, or the customer explicitly asks for a human, call request_human_attention.

MERCHANT RULES ("Teach your AI"):
{rule_lines}

Be concise, accurate, and move the customer toward their goal: UNDERSTAND THE LATEST MESSAGE -> ACT ACCORDING TO INTENT -> ONE CLEAR REPLY."""

    async def run_agent(self, merchant_id: str, user_message: str,
                        history: list[dict] | None = None,
                        conversation_id: str | None = None,
                        max_tool_rounds: int = 6) -> dict:
        """Run one assistant turn. Returns {reply, tool_trace}. Raises LLMNotConfigured."""
        if not self.llm_connected:
            raise LLMNotConfigured("OPENAI_API_KEY not set — AI agent is not connected.")

        from emergentintegrations.llm.chat import LlmChat, UserMessage

        system = await self._build_system_prompt(merchant_id)
        recent_orders_block = ""
        if conversation_id:
            conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "merchant_id": ObjectId(merchant_id)})
            if conversation and conversation.get("sales_opt_out"):
                system += "\n\nCUSTOMER SALES OPT-OUT (ABSOLUTE): This customer has declined a recommendation. Never cross-sell, upsell, recommend related products, or ask whether they want anything else. Only answer the exact request."
            # Ground the model in the real order state for this conversation so it never
            # hallucinates order numbers and can answer status/payment/tracking questions correctly.
            recent = await db.shopify_orders.find({
                "merchant_id": ObjectId(merchant_id),
                "conversation_id": ObjectId(conversation_id),
            }).sort("created_at", -1).limit(5).to_list(5)
            if recent:
                lines = []
                for order in recent:
                    items_text = ", ".join(
                        f"{it.get('title')}"
                        + (f" ({it.get('variant')})" if it.get("variant") else "")
                        + (f" x{it.get('qty')}" if (it.get('qty') or 1) != 1 else "")
                        for it in (order.get("items") or [])
                    )
                    status_bits = [
                        f"payment: {order.get('payment_status', 'unpaid')}",
                        f"fulfillment: {order.get('fulfillment_status', 'pending')}",
                    ]
                    lines.append(
                        f"- Order {order.get('order_number')}: {items_text} | total Rs. {order.get('total')} | "
                        f"payment method: {order.get('payment_method', 'COD')} | {' | '.join(status_bits)}"
                    )
                recent_orders_block = (
                    "\n\nRECENT ORDERS ALREADY PLACED FOR THIS CUSTOMER IN THIS CONVERSATION (authoritative — do NOT re-create these):\n"
                    + "\n".join(lines)
                )
                system += recent_orders_block

        # Seed the chat with the full recent dialog as ACTUAL user/assistant turns so the
        # model can see its own prior replies (including confirmations that an order was
        # already placed) and never re-trigger the same tool calls or repeat the same reply.
        initial_messages: list[dict] = [{"role": "system", "content": system}]
        last_assistant_reply: str | None = None
        if history:
            for turn in history[-20:]:
                role = turn.get("role")
                content = (turn.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    initial_messages.append({"role": role, "content": content})
                    if role == "assistant":
                        last_assistant_reply = content

        # A final steering nudge appended right before the customer's newest message
        # (as a system-role message) so the model reads it last and honours it.
        steering = (
            "REMINDER: Respond directly to the customer's newest message below. "
            "Do NOT copy or paraphrase your previous reply. "
            "Use RECENT ORDERS to reference existing orders instead of creating duplicates. "
            "Send exactly one plain-text WhatsApp message."
        )
        if last_assistant_reply:
            steering += (
                "\nYour previous reply was: \""
                + last_assistant_reply[:400].replace('"', "'")
                + "\". Your new reply MUST be materially different and must address the newest customer message."
            )
        initial_messages.append({"role": "system", "content": steering})

        # A unique session_id per turn keeps every invocation stateless on the library side;
        # the conversational memory is fully owned by us via initial_messages above.
        import uuid
        session = f"{conversation_id or f'pg-{merchant_id}'}-{uuid.uuid4().hex[:8]}"
        chat = (LlmChat(
                    api_key=os.environ["OPENAI_API_KEY"],
                    session_id=session,
                    system_message=system,
                    initial_messages=initial_messages)
                .with_model("openai", "gpt-5.4")
                .with_tools(TOOL_SCHEMAS, tool_choice="auto"))

        ctx = {"merchant_id": merchant_id, "conversation_id": conversation_id}
        tool_trace = []
        response = await chat.send_message_with_tools(UserMessage(text=user_message))
        rounds = 0
        while getattr(response, "tool_calls", None) and rounds < max_tool_rounds:
            rounds += 1
            for tc in response.tool_calls:
                args = tc.arguments if isinstance(tc.arguments, dict) else json.loads(tc.arguments or "{}")
                result = await dispatch(tc.name, args, ctx)
                tool_trace.append({"tool": tc.name, "args": args, "result": result})
                chat.add_tool_result(tc.id, json.dumps(result, default=str))
            response = await chat.send_message_with_tools()

        reply = sanitize_reply(response.content or "")

        # Output-layer safety net: if the model still returned a near-verbatim copy of the last
        # assistant reply, ask it one more time to produce a different response that addresses
        # the customer's newest message directly.
        if last_assistant_reply and reply:
            from difflib import SequenceMatcher
            similarity = SequenceMatcher(None, reply.lower().strip(), last_assistant_reply.lower().strip()).ratio()
            if similarity >= 0.85:
                retry_nudge = (
                    "Your last draft repeated your previous reply almost word-for-word. "
                    "Rewrite it so it directly answers the customer's newest message with new, useful content. "
                    "Do not restate the previous order confirmation unless the customer asked about it. "
                    "Send exactly one short plain-text WhatsApp message."
                )
                retry_response = await chat.send_message_with_tools(UserMessage(text=retry_nudge))
                retry_rounds = 0
                while getattr(retry_response, "tool_calls", None) and retry_rounds < max_tool_rounds:
                    retry_rounds += 1
                    for tc in retry_response.tool_calls:
                        args = tc.arguments if isinstance(tc.arguments, dict) else json.loads(tc.arguments or "{}")
                        result = await dispatch(tc.name, args, ctx)
                        tool_trace.append({"tool": tc.name, "args": args, "result": result})
                        chat.add_tool_result(tc.id, json.dumps(result, default=str))
                    retry_response = await chat.send_message_with_tools()
                retry_reply = sanitize_reply(retry_response.content or "")
                if retry_reply and SequenceMatcher(None, retry_reply.lower().strip(), last_assistant_reply.lower().strip()).ratio() < similarity:
                    reply = retry_reply

        return {"reply": reply, "tool_trace": tool_trace}


ai_agent = AIAgentService()

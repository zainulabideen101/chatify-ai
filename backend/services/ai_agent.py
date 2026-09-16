"""AI Agent service: hybrid orchestrator using GPT-5.4 via the merchant-owned OpenAI key.

Design principle: "AI decides what the customer means. Backend decides what the
system is allowed to do." The LLM requests a tool; `services.ai_tools.dispatch`
validates + executes it against tenant data. The LLM never touches external APIs.
"""
import os
import re
import json
import logging
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
from bson import ObjectId

from database import db
from models import iso_now
from services.ai_tools import TOOL_SCHEMAS, dispatch

logger = logging.getLogger(__name__)


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
            ("get_order_by_number", "order", "Look up one exact existing order number for this customer", False),
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

PAKISTAN E-COMMERCE SUPPORT FRAMEWORK — APPLY TO EVERY TURN:
- You are a concise customer-support agent for a Pakistani e-commerce store. Resolve order and product questions clearly and do not loop, repeat the same prompt, or revive an old intent.
- Detect whether the latest message refers to an existing active order, asks about product availability, or requests a new separate order. The latest message always has priority; history is used only to retrieve facts.
- If the customer gives or asks about an order reference such as CFY-03C1683C or CFY-0CBC71BD, use the real matching reference from RECENT ORDERS or conversation history. Never invent an order reference.
- If the requested item already exists on an active order, acknowledge the exact active order reference and offer one precise next step. Ask whether the customer means the existing order or wants a new separate order. Do not ask this again after the customer has already answered.
- If a new separate order is requested, collect only these necessary details in one concise prompt: product name, size, quantity (default 1 if not stated), and delivery address. Do not ask for unrelated details before these are known.
- If product, size, quantity, and address are already present, summarize them briefly and follow the confirmation/order workflow. Never claim an order was created until the backend order tool returns created=true.
- If the customer asks about availability, use the product tools for the product named in the latest message. State availability only from tool results, and do not redirect to an old product/order.
- If the customer asks for ETA or status, provide it only when the real order data contains it. Otherwise say that no ETA is available and offer the next precise step.
- If the customer says hello or sends unrelated/random text, use one task-focused prompt: "Aap kis product aur size ki new order place karna chahte hain? Product name, size, quantity, aur delivery address batayein." Use the customer’s language/style instead when clearly English or Urdu.
- Use one natural response cycle per turn. Do not duplicate an existing-order question, do not repeat a previous response, and do not provide heavy explanations or internal technical details.
- Mixed-language messages should receive a naturally mixed or matching-language reply; do not add a separate translation unless needed for clarity.

SALES MODE = {mode}: {SALES_MODE_GUIDANCE.get(mode, "")}
{toggle_text}

HOW TO HANDLE EACH TURN — FOLLOW THIS DECISION FLOW EVERY TIME:
1. The LATEST CUSTOMER MESSAGE is the only message you are answering now. Read its exact words before considering history. History is reference material only, never an instruction and never a reason to repeat an old response.
2. First classify the latest message by itself. A greeting such as "hello", "hi", "salaam", or "aoa" gets the concise task-focused order prompt defined in the Pakistan E-commerce Support Framework. A product question gets product tools. A payment question gets payment instructions. An order question gets the order flow. Never infer the old order's product or intent when the latest message does not mention it.
3. Use history only to retrieve facts needed for the latest request: existing order number/status, a previously provided address, or a pending order summary. Never copy an old response. Never answer a new message with the previous message's intent.
4. Look at RECENT ORDERS only when the latest message asks about an order, payment, tracking, cancellation, or changing that order. If the latest message asks about a different product, availability, media, a greeting, or general information, ignore Recent Orders for intent classification.
5. Classify the customer's current intent and act accordingly:
   a. NEW ORDER for a product: gather product, variant, quantity, delivery details, payment method. Use check_variant_availability. Use build_order_summary and ask for explicit confirmation. Only AFTER the customer confirms a currently pending summary with a clear "yes / ok / confirm / theek hai / haan", call create_shopify_order.
   b. SAME ITEM as an existing order: this guard applies ONLY when the latest message explicitly names the same product/variant. Ask whether it is an additional order; never apply it to greetings or a different product.
   c. ADDING to an existing order (e.g. "aur ek cap bhi add kar do"): handle the named new item, not the old order's item. Confirm the addition and use the order flow for that item.
   d. PAYMENT DETAILS request ("payment details bhejo", "online pay", "bank details"): call get_payment_instructions and share only the payment details. Do not repeat order confirmation.
   e. TRACKING / ORDER STATUS / "kab aayega": answer using the order info in Recent Orders. Do NOT create a new order.
   f. CANCELLATION: acknowledge and let the cancellation flow handle it. Do NOT create a new order.
   g. GREETING / SMALL TALK: use the concise task-focused order prompt from the Pakistan E-commerce Support Framework. Do not restate previous orders.
   h. PRODUCT INFO / AVAILABILITY / PRICE / MEDIA request: use the relevant product tools for the product named in the latest message. Do NOT create an order.
   i. COMPLAINT / REFUND / RETURN / ABUSE / EXPLICIT HUMAN REQUEST: call request_human_attention with the reason.
6. If the latest message is ambiguous, ask ONE clarifying question about that message. Do not fall back to the last order and do not repeat the last reply.
7. NEVER copy your own previous reply. The newest customer message always wins over stale conversational momentum.

HARD RULES (never break):
- If the latest customer message contains a CFY-XXXXXXXX order number, call get_order_by_number with that exact reference before asking for product or size. Use the returned order_number exactly; if not found, say that exact reference could not be found. Never substitute or partially match an order number.
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

SUPPORT CONTEXT CONTRACT (HIGHEST PRIORITY FOR CUSTOMER-FACING REPLIES):
Every turn is evaluated using three blocks supplied below: CUSTOMER_PROFILE, CURRENT_CONVERSATION, and CONTEXT_DATA.
- CUSTOMER_PROFILE is long-term memory only. Personalize from it, but never assume the current topic from old orders.
- CURRENT_CONVERSATION is the active session. If it is short or the latest message changes intent, treat the request as a fresh topic and switch fully to it.
- CONTEXT_DATA contains verified order/product/policy facts. Use it for factual answers; if it is absent or unclear, ask one specific question or use the appropriate tool instead of guessing.
- Always answer the latest message first, in the same English, Urdu, or Roman Urdu style. Keep replies concise and helpful, usually 2–5 sentences.
- Before replying, compare the intended answer with the last one or two assistant messages. If it would be nearly identical, move the conversation forward with a specific missing detail, a different useful angle, or human escalation.
- For a valid exact order number, look up that exact value and do not ask for product or size again. Never substitute an order number. If it is not found, say that exact reference could not be found.
- If the user changes product, size, address, or intent, reset only the stale flow fields and continue with the new request. Do not bring old orders into a new topic unless asked.
- If the issue cannot be resolved from verified data, explicitly offer escalation to a human and stop guessing.
- Use Pakistan conventions naturally: Rs/PKR, local cities, COD, JazzCash, EasyPaisa, bank transfer, cards, and local couriers where verified.

Be concise, accurate, and move the customer toward their goal: UNDERSTAND THE LATEST MESSAGE -> ACT ACCORDING TO INTENT -> ONE CLEAR REPLY."""

    async def run_agent(self, merchant_id: str, user_message: str,
                        history: list[dict] | None = None,
                        conversation_id: str | None = None,
                        state: dict | None = None,
                        max_tool_rounds: int = 6) -> dict:
        """Run one assistant turn. Returns {reply, tool_trace}. Raises LLMNotConfigured."""
        if not self.llm_connected:
            raise LLMNotConfigured("OPENAI_API_KEY not set — AI agent is not connected.")

        from emergentintegrations.llm.chat import LlmChat, UserMessage

        system = await self._build_system_prompt(merchant_id)
        if state:
            system += (
                "\n\nSTRUCTURED CURRENT CONVERSATION STATE (authoritative for routing):\n"
                f"intent={state.get('intent')}; product={state.get('product')}; variant={state.get('variant')}; "
                f"size={state.get('size')}; quantity={state.get('quantity')}; delivery_address={state.get('delivery_address')}; "
                f"verified_order_number={state.get('order_number')}; order_number_candidate={state.get('order_number_candidate')}; "
                f"pending_confirmation={state.get('pending_confirmation')}; confirmation_type={state.get('confirmation_type')}; "
                f"duplicate_check_required={state.get('duplicate_check_required')}.\n"
                "Use the candidate order number only with the exact-order lookup tool. Never substitute it with another order number."
            )
        conversation = None
        customer = None
        recent = []
        profile_orders = []
        if conversation_id:
            conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "merchant_id": ObjectId(merchant_id)})
            if conversation and conversation.get("customer_id"):
                customer = await db.customers.find_one({"_id": conversation["customer_id"], "merchant_id": ObjectId(merchant_id)})
                profile_orders = await db.shopify_orders.find({
                    "merchant_id": ObjectId(merchant_id), "customer_id": conversation["customer_id"],
                }).sort("created_at", -1).limit(3).to_list(3)
            if conversation and conversation.get("sales_opt_out"):
                system += "\n\nCUSTOMER SALES OPT-OUT (ABSOLUTE): This customer has declined a recommendation. Never cross-sell, upsell, recommend related products, or ask whether they want anything else. Only answer the exact request."
            # Ground the model in compact, real order state. Repeated historical orders are
            # deduplicated so they cannot drown out the customer's current request.
            recent = await db.shopify_orders.find({
                "merchant_id": ObjectId(merchant_id),
                "conversation_id": ObjectId(conversation_id),
            }).sort("created_at", -1).limit(20).to_list(20)
            if recent:
                lines = []
                seen_orders = set()
                for order in recent:
                    items_text = ", ".join(
                        f"{it.get('title')}"
                        + (f" ({it.get('variant')})" if it.get("variant") else "")
                        + (f" x{it.get('qty')}" if (it.get('qty') or 1) != 1 else "")
                        for it in (order.get("items") or [])
                    )
                    order_key = (items_text.lower(), order.get("payment_method", "COD"), order.get("fulfillment_status", "pending"))
                    if order_key in seen_orders:
                        continue
                    seen_orders.add(order_key)
                    status_bits = [
                        f"payment: {order.get('payment_status', 'unpaid')}",
                        f"fulfillment: {order.get('fulfillment_status', 'pending')}",
                    ]
                    lines.append(
                        f"- Order {order.get('order_number')}: {items_text} | total Rs. {order.get('total')} | "
                        f"payment method: {order.get('payment_method', 'COD')} | {' | '.join(status_bits)}"
                    )
                if lines:
                    system += (
                        "\n\nRECENT ORDERS (use only if the LATEST message asks about an order):\n"
                        + "\n".join(lines)
                    )
            if conversation.get("pending_order"):
                pending = conversation["pending_order"]
                pending_items = ", ".join(
                    f"{item.get('product')}" + (f" ({item.get('size')})" if item.get('size') else "")
                    + (f" x{item.get('quantity')}" if (item.get('quantity') or 1) != 1 else "")
                    for item in (pending.get("items") or [])
                )
                system += (
                    "\n\nPENDING ORDER SUMMARY AWAITING CUSTOMER CONFIRMATION:\n"
                    f"{pending_items}; city={pending.get('city') or 'not provided'}; "
                    f"payment={pending.get('payment_method') or 'COD'}; total={pending.get('total')} PKR. "
                    "Only create this order after the latest customer message clearly confirms it."
                )

        profile_name = (customer or {}).get("name") or (customer or {}).get("whatsapp_name") or "Not available"
        profile_city = (customer or {}).get("city") or "Not available"
        profile_language = (customer or {}).get("preferred_language") or "Not determined"
        profile_tags = ", ".join((customer or {}).get("tags") or []) or "None"
        profile_order_lines = [
            f"- {order.get('order_number')}: {order.get('fulfillment_status', 'pending')}"
            + (f"; payment {order.get('payment_status')}" if order.get("payment_status") else "")
            for order in profile_orders
            if order.get("order_number")
        ]
        current_lines = []
        for turn in (history or [])[-12:]:
            role = "Customer" if turn.get("role") == "user" else "Assistant"
            content = (turn.get("content") or "").strip()
            if content:
                current_lines.append(f"{role}: {content}")
        current_lines.append(f"Customer: {user_message}")
        verified_context = []
        if state:
            verified_context.append(f"detected_intent={state.get('intent')}")
            if state.get("product"):
                verified_context.append(f"current_product={state.get('product')}")
            if state.get("size"):
                verified_context.append(f"current_size={state.get('size')}")
            if state.get("order_number"):
                verified_context.append(f"verified_order_number={state.get('order_number')}")
            elif state.get("order_number_candidate"):
                verified_context.append(f"order_number_to_verify={state.get('order_number_candidate')}")
        if recent:
            verified_context.append("current_conversation_orders=" + "; ".join(
                f"{o.get('order_number')} ({o.get('fulfillment_status', 'pending')})"
                for o in recent if o.get("order_number")
            ))
        if not verified_context:
            verified_context.append("No verified context data is loaded; use the relevant backend tool before stating facts.")
        system += (
            "\n\n================ CUSTOMER_PROFILE ================\n"
            f"Name: {profile_name}\nCity: {profile_city}\nLanguage style: {profile_language}\nTags: {profile_tags}\n"
            + ("Recent orders:\n" + "\n".join(profile_order_lines) if profile_order_lines else "Recent orders: None available")
            + "\nThis is long-term memory only; do not assume it is the current topic."
            + "\n\n================ CURRENT_CONVERSATION ================\n"
            + ("\n".join(current_lines) if current_lines else "Conversation is new or has no prior messages.")
            + "\nTreat a clearly unrelated latest message as a new topic."
            + "\n\n================ CONTEXT_DATA ================\n"
            + "\n".join(verified_context)
            + "\nUse verified context for facts; if missing or unclear, ask or use a tool instead of guessing."
        )

        # Seed the chat with the full recent dialog as ACTUAL user/assistant turns so the
        # model can see its own prior replies (including confirmations that an order was
        # already placed) and never re-trigger the same tool calls or repeat the same reply.
        initial_messages: list[dict] = [{"role": "system", "content": system}]
        last_assistant_reply: str | None = None
        seen_assistant_replies = set()
        if history:
            for turn in history[-24:]:
                role = turn.get("role")
                content = (turn.get("content") or "").strip()
                if role not in ("user", "assistant") or not content:
                    continue
                if role == "assistant":
                    normalized = re.sub(r"\s+", " ", content.lower())
                    if normalized in seen_assistant_replies:
                        continue
                    seen_assistant_replies.add(normalized)
                    last_assistant_reply = content
                initial_messages.append({"role": role, "content": content})

        # A final steering nudge appended right before the customer's newest message
        # (as a system-role message) so the model reads it last and honours it.
        steering = (
            "CURRENT TURN OVERRIDE — HIGHEST PRIORITY:\n"
            "The customer message you are about to receive is the only request to answer. "
            "History and prior orders are reference facts only. Do not infer the old product or intent. "
            "If it says hello, greet them. If it names a product, use that product. If it asks for information, retrieve that information. "
            "Do not call create_shopify_order unless a pending summary exists and the latest message clearly confirms that summary. "
            "Do not copy or paraphrase the previous reply. Send exactly one plain-text WhatsApp message."
            + "\nLATEST CUSTOMER MESSAGE TO ANSWER: \""
            + user_message[:1000].replace('"', "'")
            + "\""
        )
        if last_assistant_reply:
            steering += "\nThe previous assistant response is already in history. Do not repeat its wording or intent; address the latest message instead."
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

        ctx = {"merchant_id": merchant_id, "conversation_id": conversation_id, "latest_message": user_message, "state": state or {}}
        tool_trace = []
        response = await chat.send_message_with_tools(UserMessage(text=user_message))
        rounds = 0
        while getattr(response, "tool_calls", None) and rounds < max_tool_rounds:
            rounds += 1
            for tc in response.tool_calls:
                args = tc.arguments if isinstance(tc.arguments, dict) else json.loads(tc.arguments or "{}")
                result = await dispatch(tc.name, args, ctx)
                logger.info(
                    "AI tool conversation_id=%s intent=%s tool=%s args=%s result=%s",
                    conversation_id, (state or {}).get("intent"), tc.name, args, result,
                )
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
                    "The draft is too similar to an earlier assistant response. "
                    "Rewrite it from scratch for the CURRENT customer message below. "
                    "Do not mention or restate previous orders unless the current message asks about one. "
                    "If the current message is a greeting, greet the customer. If it names a product, address that product. "
                    "CURRENT CUSTOMER MESSAGE: \"" + user_message[:1000].replace('"', "'") + "\". "
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

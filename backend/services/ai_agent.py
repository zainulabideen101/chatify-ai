"""AI Agent service: hybrid orchestrator using GPT-5.4 via the merchant-owned OpenAI key.

Design principle: "AI decides what the customer means. Backend decides what the
system is allowed to do." The LLM requests a tool; `services.ai_tools.dispatch`
validates + executes it against tenant data. The LLM never touches external APIs.
"""
import os
import re
import json
import logging
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
        return ""

    async def run_agent(self, merchant_id: str, user_message: str,
                        history: list[dict] | None = None,
                        conversation_id: str | None = None,
                        state: dict | None = None,
                        max_tool_rounds: int = 6) -> dict:
        """Run one assistant turn. Returns {reply, tool_trace}. Raises LLMNotConfigured."""
        if not self.llm_connected:
            raise LLMNotConfigured("OPENAI_API_KEY not set — AI agent is not connected.")

        from emergentintegrations.llm.chat import LlmChat, UserMessage

        system = ""

        # The OpenAI call uses only the model wrapper and persisted dialogue history.
        conversation = None
        customer = None
        recent = []
        profile_orders = []
        if False:
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
            if conversation and conversation.get("pending_order"):
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
        customer_profile_block = (
            f"Name: {profile_name}\nCity: {profile_city}\nLanguage style: {profile_language}\nTags: {profile_tags}\n"
            + ("Recent orders:\n" + "\n".join(profile_order_lines) if profile_order_lines else "Recent orders: None available")
        )
        current_conversation_block = "\n".join(current_lines) if current_lines else "Conversation is new or has no prior messages."
        context_data_block = "\n".join(verified_context)
        system += (
            "\n\n================ CUSTOMER_PROFILE ================\n"
            + customer_profile_block
            + "\nThis is long-term memory only; do not assume it is the current topic."
            + "\n\n================ CURRENT_CONVERSATION ================\n"
            + current_conversation_block
            + "\nTreat a clearly unrelated latest message as a new topic."
            + "\n\n================ CONTEXT_DATA ================\n"
            + context_data_block
            + "\nUse verified context for facts; if missing or unclear, ask or use a tool instead of guessing."
        )

        system = ""
        initial_messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            for turn in history[-24:]:
                role = turn.get("role")
                content = (turn.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    initial_messages.append({"role": role, "content": content})

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

        reply = response.content or ""

        return {"reply": reply, "tool_trace": tool_trace}


ai_agent = AIAgentService()

"""AI Agent service: hybrid orchestrator using GPT-5.4 (Emergent Universal Key).

Design principle: "AI decides what the customer means. Backend decides what the
system is allowed to do." The LLM requests a tool; `services.ai_tools.dispatch`
validates + executes it against tenant data. The LLM never touches external APIs.
"""
import os
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
from bson import ObjectId

from database import db
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


class AIAgentService:
    def __init__(self):
        self.tools = ToolRegistry()

    @property
    def llm_connected(self) -> bool:
        return bool(os.environ.get("EMERGENT_LLM_KEY"))

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

        return f"""You are the AI sales & customer-service assistant for "{store}", a Pakistani e-commerce store selling on WhatsApp. You act like a real human sales employee, not a bot.

LANGUAGE: Reply in the SAME language/style the customer uses — Roman Urdu, Urdu, English, or mixed. If they write Roman Urdu (e.g. "black hoodie medium mein hai?"), reply in natural Roman Urdu. Keep messages short and WhatsApp-like. Ask ONE question at a time. Use minimal, natural emoji.

SALES MODE = {mode}: {SALES_MODE_GUIDANCE.get(mode, "")}
{toggle_text}

HARD RULES (never break):
- NEVER invent inventory, prices, discounts, product specs, shipping times, or policies. ALWAYS call a tool to get real data first.
- Use check_variant_availability before confirming any product is available or stating a price.
- Use find_product_media when the customer asks for a photo/pic/video. Only claim a media is a "real photo" if its tags say so.
- Use search_knowledge_base before answering policy questions (returns, shipping, warranty, sizes).
- Use validate_address before accepting a delivery address; if incomplete, ask for exactly the missing parts.
- Before any order, use build_order_summary and show the customer the summary, then ask for explicit confirmation. Do NOT claim an order is placed (order creation happens only after backend confirmation).
- If you are unsure or the request is a complaint/refund/angry customer, call request_human_attention.

MERCHANT RULES ("Teach your AI"):
{rule_lines}

Be concise, accurate, and move the customer toward their goal: UNDERSTAND → HELP → RECOMMEND IF RELEVANT → COMPLETE ACTION."""

    async def run_agent(self, merchant_id: str, user_message: str,
                        history: list[dict] | None = None,
                        conversation_id: str | None = None,
                        max_tool_rounds: int = 6) -> dict:
        """Run one assistant turn. Returns {reply, tool_trace}. Raises LLMNotConfigured."""
        if not self.llm_connected:
            raise LLMNotConfigured("EMERGENT_LLM_KEY not set — AI agent is not connected.")

        from emergentintegrations.llm.chat import LlmChat, UserMessage

        system = await self._build_system_prompt(merchant_id)
        if history:
            transcript = "\n".join(
                f"{'Customer' if m['role'] == 'user' else 'You'}: {m['content']}"
                for m in history[-12:]
            )
            system += f"\n\nCONVERSATION SO FAR:\n{transcript}"

        session = conversation_id or f"pg-{merchant_id}"
        chat = (LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=session, system_message=system)
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

        return {"reply": response.content or "", "tool_trace": tool_trace}


ai_agent = AIAgentService()

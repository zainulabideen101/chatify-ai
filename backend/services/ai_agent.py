"""AI Agent service architecture: tool registry, tool-execution contract, and the
hybrid orchestration interface.

Design principle: "AI decides what the customer means. Backend decides what the
system is allowed to do." The LLM may REQUEST a tool; the backend validates the
request against the tenant + business rules and executes it. The LLM never gets
unrestricted access to external APIs.

The LLM is NOT connected in this foundation build (per configuration). Wiring it
later requires only implementing `LLMClient.complete` (Emergent Universal Key,
GPT-5.4) and populating tool executors — the registry and contract below stay
the same.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ToolSpec:
    name: str
    category: str
    description: str
    params: dict = field(default_factory=dict)
    # side_effect=True tools mutate external systems and require strict validation
    side_effect: bool = False
    tenant_aware: bool = True


@dataclass
class ToolResult:
    ok: bool
    data: dict = field(default_factory=dict)
    confidence: Confidence = Confidence.HIGH
    error: Optional[str] = None


class ToolRegistry:
    """Catalog of controlled tools the LLM may request. Every tool is tenant-aware."""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._executors: dict[str, Callable] = {}
        self._register_defaults()

    def register(self, spec: ToolSpec, executor: Callable = None):
        self._tools[spec.name] = spec
        if executor:
            self._executors[spec.name] = executor

    def list(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "side_effect": t.side_effect,
                "tenant_aware": t.tenant_aware,
                "implemented": t.name in self._executors,
            }
            for t in self._tools.values()
        ]

    def _register_defaults(self):
        defs = [
            # product
            ("search_products", "product", "Search catalog by query/attributes", False),
            ("get_product", "product", "Fetch a product by id", False),
            ("get_product_variant", "product", "Fetch a variant by id", False),
            ("check_inventory", "product", "Check current inventory for a variant", False),
            ("get_product_price", "product", "Get current price for a variant", False),
            ("get_collection_products", "product", "List products in a collection", False),
            # customer
            ("find_customer", "customer", "Find a customer by phone/name", False),
            ("create_customer", "customer", "Create a customer record", True),
            ("update_customer", "customer", "Update a customer record", True),
            ("get_customer_orders", "customer", "List a customer's orders", False),
            ("get_customer_preferences", "customer", "Get stored customer memory", False),
            # media
            ("find_product_media", "media", "Find media for a product by tag/type", False),
            ("send_image", "media", "Send an image over WhatsApp", True),
            ("send_video", "media", "Send a video over WhatsApp", True),
            ("send_product_media", "media", "Send appropriate product media", True),
            # order
            ("create_order_session", "order", "Start an order session", True),
            ("add_item_to_order", "order", "Add an item to the order session", True),
            ("remove_item_from_order", "order", "Remove an item", True),
            ("change_quantity", "order", "Change item quantity", True),
            ("change_variant", "order", "Change item variant", True),
            ("calculate_order_total", "order", "Compute order total", False),
            ("validate_order", "order", "Validate order before creation", False),
            ("create_draft_order", "order", "Create a Shopify draft order", True),
            ("create_shopify_order", "order", "Create the final Shopify order", True),
            # address
            ("validate_address", "address", "Validate deliverability of an address", False),
            ("detect_missing_address_information", "address", "Detect missing address parts", False),
            # shipping
            ("get_shipping_options", "shipping", "Get available shipping options", False),
            ("calculate_shipping_cost", "shipping", "Calculate shipping cost", False),
            # whatsapp
            ("send_message", "whatsapp", "Send a WhatsApp text message", True),
            ("send_template", "whatsapp", "Send an approved WhatsApp template", True),
            # ai / escalation
            ("request_human_attention", "escalation", "Flag conversation for a human", True),
            ("pause_ai", "escalation", "Pause AI auto-responses", True),
            ("resume_ai", "escalation", "Resume AI auto-responses", True),
            # analytics
            ("record_ai_action", "analytics", "Record an AI action event", True),
            ("record_recommendation", "analytics", "Record a recommendation event", True),
            ("record_conversion_event", "analytics", "Record a conversion event", True),
        ]
        for name, cat, desc, side in defs:
            self.register(ToolSpec(name=name, category=cat, description=desc, side_effect=side))


class LLMNotConfigured(Exception):
    pass


class AIAgentService:
    """Hybrid orchestrator. Holds the tool registry and the conversation loop
    contract. Executes deterministic backend tools; requests LLM only for NLU
    and response generation.
    """

    def __init__(self):
        self.tools = ToolRegistry()

    @property
    def llm_connected(self) -> bool:
        import os
        return bool(os.environ.get("EMERGENT_LLM_KEY"))

    async def process_inbound_message(self, merchant_id: str, conversation_id: str, text: str) -> dict:
        """Entry point for an inbound customer message.

        Foundation build: the LLM is intentionally not connected, so we do not
        fabricate a reply. Returns a structured 'needs_configuration' result the
        WhatsApp webhook handler records without sending a fake message.
        """
        if not self.llm_connected:
            raise LLMNotConfigured(
                "AI agent LLM (GPT-5.4 via Emergent Universal Key) is not connected. "
                "Message stored; no automated reply generated in the foundation build."
            )
        # Future: NLU -> intent -> tool requests -> backend validation/execution -> response.
        raise LLMNotConfigured("LLM orchestration not yet implemented.")


ai_agent = AIAgentService()

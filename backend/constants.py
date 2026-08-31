"""Domain constants: enums for order states, intents, confidence, sales modes, media tags."""

# Order session state machine
ORDER_STATES = [
    "BROWSING",
    "PRODUCT_SELECTED",
    "VARIANT_SELECTED",
    "QUANTITY_CONFIRMED",
    "CUSTOMER_DETAILS",
    "ADDRESS_VALIDATION",
    "ORDER_REVIEW",
    "CUSTOMER_CONFIRMATION",
    "SHOPIFY_ORDER_CREATION",
    "ORDER_CREATED",
    "ABANDONED",
]

# Allowed forward transitions for the deterministic order state machine
ORDER_TRANSITIONS = {
    "BROWSING": ["PRODUCT_SELECTED", "ABANDONED"],
    "PRODUCT_SELECTED": ["VARIANT_SELECTED", "BROWSING", "ABANDONED"],
    "VARIANT_SELECTED": ["QUANTITY_CONFIRMED", "PRODUCT_SELECTED", "ABANDONED"],
    "QUANTITY_CONFIRMED": ["CUSTOMER_DETAILS", "VARIANT_SELECTED", "ABANDONED"],
    "CUSTOMER_DETAILS": ["ADDRESS_VALIDATION", "ABANDONED"],
    "ADDRESS_VALIDATION": ["ORDER_REVIEW", "CUSTOMER_DETAILS", "ABANDONED"],
    "ORDER_REVIEW": ["CUSTOMER_CONFIRMATION", "ABANDONED"],
    "CUSTOMER_CONFIRMATION": ["SHOPIFY_ORDER_CREATION", "ORDER_REVIEW", "ABANDONED"],
    "SHOPIFY_ORDER_CREATION": ["ORDER_CREATED", "ORDER_REVIEW"],
    "ORDER_CREATED": [],
    "ABANDONED": ["BROWSING"],
}

CONVERSATION_INTENTS = [
    "PRODUCT_SEARCH", "PRODUCT_QUESTION", "PRICE_QUERY", "INVENTORY_QUERY",
    "MEDIA_REQUEST", "PRODUCT_RECOMMENDATION", "CROSS_SELL", "UPSELL",
    "ORDER_REQUEST", "ORDER_STATUS", "SHIPPING", "RETURN", "EXCHANGE",
    "COMPLAINT", "PAYMENT", "HUMAN_REQUEST", "OTHER",
]

CONFIDENCE_LEVELS = ["HIGH", "MEDIUM", "LOW"]

CONVERSATION_MODES = ["AI_ACTIVE", "HUMAN_ACTIVE", "AI_PAUSED", "CLOSED"]

SALES_MODES = ["SUPPORT_ONLY", "HELPFUL_SALES", "AGGRESSIVE_SALES"]

COD_RISK_LEVELS = ["LOW", "MEDIUM", "HIGH"]

MEDIA_TAGS = [
    "real_photo", "customer_photo", "wearing_photo", "wearing_video",
    "product_video", "fabric_closeup", "color_reference", "packaging",
    "size_reference", "how_to_use",
]

TEMPLATE_USE_CASES = [
    "order_confirmation", "order_shipped", "order_out_for_delivery",
    "order_delivered", "address_clarification", "abandoned_order",
    "payment_reminder",
]

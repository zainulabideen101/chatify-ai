# PRD — AI WhatsApp Commerce Manager for Shopify

## Original Problem Statement
Multi-tenant SaaS for Shopify merchants: an AI-powered WhatsApp sales & customer-service
manager that connects a merchant's Shopify store + Meta WhatsApp Business account, understands
customers in Roman Urdu / Urdu / English / mixed, handles conversations autonomously, sends
product media, recommends products, takes orders, validates info, creates Shopify orders,
recovers abandoned orders, and reports actionable analytics. Instruction: build the production
foundation first (auth, multi-tenancy, DB schema, dashboard shell, service architecture,
integration interfaces) WITHOUT mocking Shopify / Meta WhatsApp / LLM, then stop and report.

## User Choices
- LLM: GPT-5.4 (NOT connected yet — interface only, no tool-calling wired)
- Auth: JWT email/password (custom)
- Integrations: build real service interfaces only; do not connect Shopify/WhatsApp/LLM now
- Media: data model + upload interface (no object storage yet)
- UI: clean/minimal light SaaS; currency Rs. (PKR)

## Architecture
- Backend: FastAPI (modular). `server.py` wires routers; `database.py` (Mongo + PyObjectId +
  recursive serialize); `security.py` (bcrypt + JWT); `deps.py` (auth + tenant scoping);
  `constants.py` (order states, intents, confidence, sales modes, media tags);
  `models.py`; `audit.py`; `seed.py` (owner + demo tenant, idempotent, TTL indexes).
- Services (`/backend/services`): `shopify_service` (OAuth + Admin API interface, HMAC verify,
  raises ShopifyNotConfigured — no fake data), `whatsapp_service` (Cloud API interface, webhook
  verify/signature), `ai_agent` (ToolRegistry of 36 controlled tools, Confidence enum, hybrid
  orchestrator — raises LLMNotConfigured), `commerce` (deterministic AddressValidator,
  CODRiskAssessor, OrderStateMachine), `crypto` (Fernet encryption of 3rd-party tokens).
- Routers: auth, merchant (settings/ai-settings/rules/kb/templates/audit), integrations
  (shopify/whatsapp/webhooks), commerce (products/media/customers/orders/abandoned/tools),
  inbox (conversations/takeover/messages/ai-tools), analytics (dashboard + analytics).
- Frontend: React + Tailwind + shadcn, Space Grotesk / IBM Plex fonts. AuthContext (Bearer token
  in localStorage + httpOnly cookies), responsive Layout w/ mobile drawer, 12 dashboard sections.

## Multi-Tenancy
Every collection carries `merchant_id`; every query is tenant-scoped via `get_tenant_id`.
Verified by testing agent: cross-tenant reads return 404, new merchant sees empty tenant.

## Implemented (2026-06 — Foundation, Phase 1)
- JWT auth: register (creates merchant + owner + default AI settings), login (bcrypt, brute-force
  lockout keyed on email + TTL index), logout, /me, /refresh. Owner account: techgear008@gmail.com.
- Dashboard shell: AI Sales Overview KPIs, recent conversations/orders, needs-attention,
  abandoned opportunities, top products (all with empty states).
- Conversations inbox: 3-pane, human takeover / return-to-AI, human send (real WhatsApp call when
  connected, else stored + delivered=false), AI summary + customer memory + suggestion panels.
- Orders, Customers (COD risk modal), Products, Media Library (add/delete + tags), AI Agent
  (sales mode, 8 toggles, Teach-your-AI rules, 36-tool registry view), WhatsApp connect,
  Templates (CRUD), Knowledge Base (CRUD), Analytics (revenue attribution chart, cross/upsell,
  COD, customers), Settings (store profile + Shopify OAuth connect + sync).
- Deterministic tools live: address validation, COD risk scoring, order state machine.
- Security: encrypted token storage, webhook HMAC/signature verification, audit logging,
  input validation (Literal enums + ranges), explicit CORS, tenant isolation.
- Seeded demo tenant (TechGear Threads) for a meaningful shell — illustrative tenant data,
  NOT faked API responses.

## Remaining Backlog (by phase)
- P0 (next): Connect Shopify (real Partner app creds → OAuth exchange, catalog sync service,
  webhooks for inventory/orders). Requires SHOPIFY_API_KEY / SHOPIFY_API_SECRET.
- P0: Connect GPT-5.4 via Emergent Universal Key — NLU, intent classification, tool-calling loop,
  conversation + customer memory use, confidence system, response generation (Roman Urdu).
- P1: Meta WhatsApp Embedded Signup, live inbound webhook → AI pipeline, template sending,
  media sending; abandoned-order recovery scheduler; AI suggested replies during human takeover.
- P1: Order session flow end-to-end (add/remove/qty/variant → validate → draft → Shopify order),
  explicit confirmation, idempotency.
- P2: AI media auto-classification, RAG over knowledge base, revenue attribution refinement,
  cross/upsell tracking wiring, object storage for media uploads, role-based multi-user per merchant.

## Config / Credentials Required to Activate
- Shopify: SHOPIFY_API_KEY, SHOPIFY_API_SECRET (Partner app), webhook URL /api/webhooks/shopify
- Meta WhatsApp: META_APP_ID, META_APP_SECRET, META_VERIFY_TOKEN; per-merchant WABA token
- LLM: EMERGENT_LLM_KEY (Emergent Universal Key) for GPT-5.4
- APP_ENCRYPTION_KEY / JWT_SECRET already set.

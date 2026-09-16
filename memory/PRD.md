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


## Platform Integrations Status
- Meta WhatsApp Embedded Signup is implemented as a platform-owned, tenant-scoped flow. Each tenant stores its own WABA ID, Phone Number ID, encrypted authorization token, connection state, and webhook routing under `merchant_id`.
- Live Meta activation is **NOT CONFIGURED** until the platform owner provides `META_APP_ID`, `META_APP_SECRET`, `META_CONFIG_ID`, and `META_VERIFY_TOKEN` in backend secrets and completes Meta app/webhook setup.
- `EMERGENT_LLM_KEY` is configured server-side; GPT-5.4 playground verification is working.
- Frontend fail-closed behavior is verified: missing Meta platform config shows the warning, hides Embedded Signup, and keeps manual fields available.

## 2026-02 — Conversations UX + AI Persona Hardening
- Inbox thread auto-scrolls to the latest message; jump-to-bottom on thread switch, smooth-scroll on new message arrival.
- WhatsApp-style read-receipt ticks on outbound messages: `queued` → single grey ✓, `sent` → single ✓, `delivered` → double ✓, `read` → double blue ✓, `failed` → red label. Driven by Meta status webhook updates on `messages.status`.
- Unread highlighting in conversation list: soft green row background, bold customer name, unread count badge (99+ cap). `unread_count` is atomically `$inc`'d on inbound webhook and reset on `GET /api/conversations/{cid}`, which also stamps `read_by_human_at` on previously unread inbound messages.
- Auto-handover: configurable `ai_settings.auto_handover_seconds` (default 30, range 0–3600; 0 disables). When a conversation is HUMAN_ACTIVE past the timeout without a human reply and has unread inbound, a background sweep on inbox polls flips it back to AI_ACTIVE and triggers an AI reply with full history.
- Return-to-AI: `POST /api/conversations/{cid}/return-to-ai` now background-schedules an AI response when the last message is inbound, so customers aren't left hanging when the human hands the chat back.
- Shared helper `services/conversation_ai.generate_and_send_ai_reply` centralises history-building, tool loop, sanitisation, and WhatsApp send. Uses conditional Mongo update (`mode=AI_ACTIVE`) to avoid stomping a concurrent takeover.
- AI persona: system prompt rewritten with strict rules — NO emojis, NO markdown/asterisks/underscores/backticks, formal professional tone, and defined behaviour for abusive customers (stay calm, brief, escalate via `request_human_attention`). A defence-in-depth output-layer `sanitize_reply` strips emojis (Unicode categories So/Sk), markdown emphasis, and stray underscores while preserving URLs, IDs, and multilingual text.
- Merchant setting UI: new "Auto-return to AI" input on the AI Agent page.
- Tests: 22 new backend tests in `tests/test_conversations_ai_features.py`, all passing.


## 2026-02 — AI Context Loop Fix (v2, robust)
- Symptom 1: AI kept re-creating identical orders on every inbound turn ("order place ho gaya" repeated 6x for unrelated messages).
- Symptom 2 (after v1 fix): AI over-corrected and refused legitimate new orders ("Classic cap ka bhi order krna hai" → "order already placed" loop).
- Root causes: history was passed as a static system-prompt transcript (LLM ignored it); ordering guardrail was time-based instead of item-based; `[[MESSAGE_BREAK]]` and paragraph-split logic caused 3–4 outbound messages per turn; `_avoid_repeated_payment_details` regex was brittle.
- Fix (`services/ai_agent.py`):
  - History is now passed to `LlmChat` via `initial_messages` as real user/assistant turns (last 20). Unique per-turn session_id — library holds no cross-call state.
  - System prompt rewritten with a step-by-step decision tree (NEW_ORDER / SAME_ORDER / ADD / PAYMENT / TRACKING / CANCEL / GREETING / PRODUCT_INFO / COMPLAINT), and an explicit "never copy your last reply" rule.
  - A "RECENT ORDERS ALREADY PLACED" section is injected into the system prompt from live DB data so the AI can reference existing orders without hallucinating.
  - A final steering system message reiterates: respond to the newest message, do not repeat, send ONE message.
  - Output-layer safety net: if the LLM's draft is ≥85% similar (SequenceMatcher) to the last outbound message, a second LLM call is made with a stronger nudge; the more-different draft wins.
  - `sanitize_reply` neutralises any stray `[[MESSAGE_BREAK]]` marker → newline.
- Fix (`services/ai_tools.py`):
  - `create_shopify_order` guardrail is now **item-signature aware**, not time-based. It only blocks when the SAME product+variant+quantity set already exists as an active (non-cancelled) order for this conversation. Different items always pass through.
  - On duplicate, returns `{created:false, duplicate:true, existing_order_number, existing_items}` so the LLM answers with the existing order number instead of looping.
- Fix (`services/conversation_ai.py`): removed `[[MESSAGE_BREAK]]` splitting and `_avoid_repeated_payment_details` regex hack. Every AI turn now sends exactly ONE WhatsApp message. Old unit tests replaced with the new single-message policy tests.


## 2026-02 — Latest-Turn AI Context Guard
- Root cause of the Zaheer Uddin loop: the prompt treated every existing order as globally relevant, repeated assistant replies were re-fed as equal-weight history, and `create_shopify_order` could be requested without a currently pending order summary. The model consequently mapped greetings and unrelated product questions back to the old Black Hoodie intent.
- Latest-turn priority is now explicit: the newest inbound text is quoted as the highest-priority instruction, history is reference-only, and the model must classify the current message before consulting order history.
- Duplicate assistant replies are removed from the in-memory history context; repeated orders are compacted by item/status before prompt injection.
- `build_order_summary` now persists a tenant-scoped `pending_order`. `create_shopify_order` is backend-blocked unless a pending summary exists, the latest message is an order confirmation/request, and the requested cart matches that pending summary. This prevents any accidental order mutation for greetings, product availability, payment, tracking, or unrelated questions.
- The AI now receives explicit current-turn guidance for greetings, named products, information requests, and ambiguous messages, with no previous reply text echoed into the steering prompt.


## 2026-02 — Pakistan Support Framework
- Added the user-provided Pakistan e-commerce support framework directly to the OpenAI GPT-5.4 system prompt.
- The framework enforces concise bilingual support, exact real order references, availability handling, one-time existing-vs-new order clarification, minimal new-order fields (product, size, quantity default 1, address), ETA only from real data, and a task-focused prompt for greetings/random text.
- Conflicting generic greeting instructions were aligned to the framework; backend pending-order and `created=true` safeguards remain authoritative.


## 2026-02 — Deterministic Intent and Exact Order Routing
- Added `services/conversation_state.py` for latest-turn intent extraction across greetings, availability, new orders, tracking, delivery estimates, modifications, cancellation, and Urdu/Roman Urdu confirmations.
- Conversation state now stores current product, size, quantity, address, verified/candidate order number, pending confirmation, duplicate-check flags, last message/intent, flow intent, and state version.
- Topic changes replace stale product/size/address flow state and clear pending duplicate/order summaries; duplicate checks run only for new-order intent.
- Added exact `get_order_by_number` tool. It scopes lookups by tenant and customer, uses the extracted full CFY reference without partial matching, verifies the returned reference, and logs mismatches without exposing internal IDs to customers.
- New-order creation is scoped to the structured current state, same-customer active orders, exact cart signatures, and a current pending summary. A customer’s explicit confirmation can advance a duplicate-check flow once; a declined confirmation clears it.
- `conversation_ai` now refreshes the conversation after persistence, updates structured state before the LLM call, passes state into the prompt/tool context, and logs intent, extracted fields, state transitions, and tool activity.


## 2026-02 — Structured OpenAI Input Blocks
- Added the requested four-block payload to every initial OpenAI customer turn: `CUSTOMER_PROFILE`, `CURRENT_CONVERSATION`, `CONTEXT_DATA`, and `USER_MESSAGE`.
- `CUSTOMER_PROFILE` is built from tenant-scoped customer memory and the latest three customer orders; it is explicitly marked as long-term context only.
- `CURRENT_CONVERSATION` contains recent user/assistant messages plus the current message; `CONTEXT_DATA` contains the structured intent/current product/size/order reference and verified current-conversation order facts.
- Added the requested base system contract for session/topic switching, IDLE/TAKING_ORDER/CONFIRMING_ORDER/CHECKING_STATUS/CANCELLATION_FLOW/ESCALATED states, no repetition, bilingual Pakistan support, exact order references, and escalation.


## 2026-02 — Custom Prompt Removal
- Removed the custom Pakistan support/base system prompt from `services/ai_agent.py` and reduced `_build_system_prompt` to an empty system message.
- Removed the custom output sanitization and retry instructions from the agent runtime. The OpenAI GPT-5.4 wrapper and existing tool-calling integration remain enabled.
- Conversation state and backend tool safeguards remain available independently of the removed prompt instructions.

# Phase 2 behavior diff report

## Routes
- **Before:** free from/to among UA/RU/BY (any pair including RU↔BY).
- **After:** only 4 labeled choices — UA→RU, UA→BY, RU→UA, BY→UA. `is_allowed_route()` rejects RU↔BY, same-country, EU.

## Application flow
- **Before:** collect doc → countries/cities → volume → urgency → **name/phone/email/best_time** → then quote + admin notify.
- **After (quote-first):** role (sender/receiver) → route → cities → doc type → volume → urgency → **show price/ETA** → «Оформить заявку» → name → phone → confirm → ticket → notify → thanks. Email/best_time removed from flow.

## Pricing / ETA
- **Before:** €65/€85 (≤50g/≤100g); ETA 21–29 / 27–29 style; reverse routes “needs confirmation”.
- **After:** from **€95** ordinary for envelope ≤500g; urgent higher (€145); >500g по согласованию; ETA **10–14** business days on all allowed routes.

## Menu
- **Before:** `/consult`, `/reset`, `/news`.
- **After:** Отправить документы, Получить документы (role=receiver, same quote-first), Отследить отправление, Что можно отправлять, Связаться с менеджером.

## Tracking / tickets
- **New:** ticket `DBxxxx` on create; `shipments` table with status pipeline `accepted → departed for hub → at hub → destination country → delivered`; `/track` + menu.

## Manager card
- Includes ticket, role, route/cities, doc type, volume, urgency, name/phone, source/ref, timestamp. Plain text (no fragile Markdown).

## AI
- **Before:** OpenAI `gpt-4o-mini` via `OPENAI_API_KEY`.
- **After:** xAI `grok-4.5` via `openai` SDK + `base_url=https://api.x.ai/v1`, env `XAI_API_KEY`. Logs `[xAI]`. Graceful off if key missing.

## DB
- Kept: `chat_history`, `user_state`, `leads`, `processed_updates` (webhook idempotency).
- Added: `shipments` (+ optional `leads.ticket`).

## Tests
- `test_phase2.py` covers allow/deny routes, price 95, ETA 10–14, ticket `DB\d+`, ordered track statuses.

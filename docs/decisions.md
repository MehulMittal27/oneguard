# Decisions log

Choices made where the spec, the data, or the platform disagreed. Newest first.

| Date | Decision | Why | Where |
|---|---|---|---|
| 2026-09-24 | team docs reconciled with core docs; database stack, timeout resolve, models path fixed | conflict list from the team-docs commit | CLAUDE.md / AGENTS.md rule 6, Stack; architecture.md Dependencies, Deployment; api-contract.md header, §3.1, §3.4, §3.5, §4, §6; database.md §1; team-contract.md §3.1; team-plan.md P1-0, P5-2; README.md Quick start |
| 2026-09-24 | Use every provided field and endpoint: step 1 names `authority_status` and `card_status_at_attempt`; C7 covers `order_cancellable`; `merchant_mcc` secondary evidence for C8; `merchants.recurring_capable` evidence for A6; W6 price outside catalogue range (weak); `HistoryIndex.agent_history` / `item_price_range`; `DryRunResult.agent_history`; reconciliation via `GET /v1/events` as well as `context`; startup pack check against `/v1/reference-data` | coverage audit of the data pack and API | rules.md §4 step 1, C7, C8, A6, W6; api-contract.md §2, §3.4; database.md §3; team-plan.md P1-1, P3-1, P5-2 |
| 2026-09-24 | Frontend contract merged into docs/api-contract.md; frontend/API-CONTRACT.md is a pointer | One canonical contract; the backend-facing requirements (envelopes, consistency matrix, check wording, checklist) now live in it | api-contract.md §1.0, §3.1a, §3.9, Appendix A; frontend/API-CONTRACT.md |
| 2026-09-24 | Spend meter reads `Mandate.usage` first, check wording second | `usage` is the engine ledger's own view; parsing limits back out of prose fails silently (the fixture's "across any rolling 7 days" never matched) | frontend/src/lib/spend.ts `limitsFromMandate`; api-contract.md §3.9 |
| 2026-09-24 | `Decision.explanation_source` ('template' \| 'model'); C6 may return the template message first and the model rewrite on a later poll | Viseca Q&A | api-contract.md §2, §3.4, §6 |
| 2026-09-24 | C8 no longer bound to a hash of the step-up rendering; the step-up still renders the complete purchase | Viseca Q&A | api-contract.md §3.5 |
| 2026-09-24 | Expired step-up: backend posts `/resolve` `decline` with the timeout message; `Decision.resolved_by: 'customer' \| 'timeout'` (NEW) | Viseca Q&A | api-contract.md §3.5 |
| 2026-09-24 | Tier 2 extraction and Tier 3 explanation use the compiler's provider interface (OpenAI first, model-agnostic); soft signals unchanged | Viseca Q&A | api-contract.md §3.7 |
| 2026-09-24 | P8: with every model off, each decision is identical or more cautious; models never change a decision the deterministic rules have made | Viseca Q&A | rules.md P8 |
| 2026-09-24 | Three tiers: 1 deterministic rules; 2 constrained LLM fact extraction only when a rule is `unknown` (schema-validated, 1.5 s, `source: model`); 3 LLM explanation rewrite after posting, template first | Viseca Q&A | rules.md §4a |
| 2026-09-24 | M5: a purchase that fails C2 only because of reservations is Ask, and the message names the waiting order | Viseca Q&A | rules.md M5 |
| 2026-09-24 | Q2 closed: expiry posts `/resolve` `decline` ("No answer within 120 s; nothing was approved", `resolved_by: timeout`); not spent | Viseca Q&A | rules.md Q2 |
| 2026-09-24 | Q6 closed: decline anything received after revoke; UI shows cancelled once the platform confirms | Viseca Q&A | rules.md Q6 |
| 2026-09-24 | Q7 closed: known shop is customer-level; Viseca handles it | Viseca Q&A | rules.md Q7 |
| 2026-09-24 | Q8 closed: superseded by the new M5 | Viseca Q&A | rules.md Q8 |
| 2026-09-24 | Q11: hidden scenarios exist at judging; C12 in scope, tested with invented instructions | Viseca Q&A | rules.md Q11 |
| 2026-09-24 | E8: after posting, an LLM may rewrite the explanation from structured evidence only; it never sees or alters the decision | Viseca Q&A | rules.md E8 |
| 2026-09-24 | AU0008 with AU0006 pending (reserved) → step_up [C2, M5] | Viseca Q&A | acceptance-oracle.yaml AU0008 |
| 2026-09-24 | Four invented instructions with expected typed rules; a phrase with no field stays `unknown` and becomes an open question | Viseca Q&A | acceptance-oracle.yaml `unseen_instructions` |
| 2026-09-24 | New typed-rule fields for C12: `items[].unit_price_chf`, `items[].quantity`, `merchant.merchant_country`, `authorization.delivery_by`, `authorization.weekday`, `authorization.local_hour` (Europe/Zurich) | Viseca Q&A | api-contract.md §3.3 |
| 2026-09-24 | `oneguard/llm/` provider interface (OpenAI first, Anthropic stub) shared by compiler, tier-2 extraction and tier-3 explanation | Viseca Q&A | architecture.md |
| 2026-09-24 | Dependencies: `openai` added, `anthropic` kept optional | Viseca Q&A | architecture.md |
| 2026-09-24 | Latency: tier 2 ≤ 1.5 s inside the 2 s budget; tier 3 after posting, outside it | Viseca Q&A | architecture.md |
| 2026-09-24 | CLAUDE.md rule 1: no model decides; models may extract a fact, rewrite the explanation after posting, or answer agent_directed via Laya (mirrored to AGENTS.md) | Viseca Q&A | CLAUDE.md, AGENTS.md |
| 2026-09-24 | `openai` added to the `compiler` extra alongside `anthropic` | Viseca Q&A | backend/pyproject.toml |
| 2026-09-24 | `ONEGUARD_LLM_PROVIDER=openai` and `OPENAI_API_KEY`; `ANTHROPIC_API_KEY` kept as the alternative; `ONEGUARD_COMPILER` removed; VITE_ lines removed (frontend has its own .env.example) | Viseca Q&A | .env.example |
| 2026-09-24 | A6 (hidden recurring cost) is Ask unless C10 is stated | Recurring cost is the risk; a customer may still want the plan; Ask is the useful intervention | rules.md §7, Q10 |
| 2026-09-24 | A1 flags the purchase, not the shop for the run | Otherwise AU0042/AU0045 could never approve at PixelHarbor | rules.md §7 |
| 2026-09-24 | W4 suppressed within a stated per-order limit | Fires on 7/11 SCEN0004 purchases otherwise | rules.md §8 |
| 2026-09-24 | Known shop = customer-level, pending Viseca answer | AU0044; issuer may only see own cards | rules.md Q7 |
| 2026-09-24 | Each purchase judged on its own (no fulfilment counter) | Viseca notes call AU0023 compliant and AU0042 a legitimate re-quote | rules.md Q1 |
| 2026-09-24 | Laya used for one question: agent_directed (threshold 0.6, 500 ms timeout, loaded once at startup, called from a thread pool). recurring_billing and item_kind dropped. | Spike: injections 0.71/0.83, no false positive above 0.38, P95 175 ms for three questions, 48 ms for agent_directed alone (P50 28 ms); recurring 0.46 miss on the only example; 13/13 monitors misclassified | signals.py |

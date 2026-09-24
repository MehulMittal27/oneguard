# Decisions log

Choices made where the spec, the data, or the platform disagreed. Newest first.

| Date | Decision | Why | Where |
|---|---|---|---|
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

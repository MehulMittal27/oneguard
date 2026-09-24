# OneGuard — API contract (frontend ⇄ backend)

Version 1.0 · 24 Sep 2026 · replaces `.claude/contracts/api-contract.md`

This contract adopts the frontend's existing shapes **unchanged** and only adds fields.
Every addition is marked `NEW` and is optional on the wire, so the current UI keeps
working before any of it is rendered. If this file and `backend/oneguard/api/models.py`
disagree, `models.py` wins and this file gets fixed in the same commit.

---

## 0. Principles

1. **The UI never decides.** It renders what `/api` returns. Every outcome, spend total,
   injection flag and countdown deadline originates in the backend.
2. **`/api` is a wrapper, not a proxy.** The backend talks to the Viseca sandbox
   (bearer key server-side only) and to the decision engine. The browser knows only `/api`.
3. **The engine is deterministic.** No generative model in the live decision path. The
   optional small decision model (Laya) supplies evidence only and can never approve.
4. **Merchant text is data.** `item_details`, `item_name`, `merchant.name`,
   `purchase_description` are passed through as plain strings and labelled untrusted.
5. **Nothing keys on scenario id, request id or replay position** in engine code. Scenario
   ids appear only in operator endpoints.
6. **Decisions are explained** in the four terms the judges use: *permitted* (policy
   applied), *evidence*, *why* (message + counterfactual), *control* (step-up / revoke).

---

## 1. Endpoints

### 1.0 Envelopes

The envelope is **not uniform**; the client depends on the exact shape per endpoint
(merged from the former frontend/API-CONTRACT.md §1.2).

| Style | Endpoints | Shape |
| --- | --- | --- |
| Wrapped in a named key | C12, C6, C10, C3 | `{ "customers": [...] }`, `{ "decisions": [...] }`, `{ "accounts": [...] }`, `{ "mandate": {...} \| null }` |
| Bare object | C1, C2, C4 | The `PolicyDraft` / `Mandate` object at the top level, no wrapper |
| No body | C5, C8 | `204 No Content` |

The frontend ignores unknown fields, so additions are safe. Removing or renaming a field
(or changing its type or null-ability) is breaking.

### 1.1 Customer-facing (called by the UI)

| # | Method | Path | Request | Response | Errors |
|---|---|---|---|---|---|
| C12 | GET | `/api/customers` | — | `{ customers: Customer[] }` | |
| C10 | GET | `/api/customers/{customer_id}/accounts` | — | `{ accounts: Account[] }` | 404 |
| C6 | GET | `/api/customers/{customer_id}/decisions` | — | `{ decisions: Decision[] }` newest first | 404 |
| C1 | POST | `/api/cards/{card_id}/policy-drafts` | `{ instruction }` **or** `{ form: FormInput }` | `PolicyDraft` | 404 card · 422 neither/both · 504 compiler timeout |
| C2 | POST | `/api/policy-drafts/{draft_id}/confirm` | `{ checks: RuleCheck[], uncertainty_policy, open_questions }` | `Mandate` | 404 draft · 409 draft already confirmed · 422 unknown check id · 409 `lint_failed` (see §3.2) |
| C3 | GET | `/api/cards/{card_id}/policy` | — | `{ mandate: Mandate \| null }` | 404 card |
| C4 | POST | `/api/cards/{card_id}/policy/tighten` | `{ add_checks: RuleCheck[], uncertainty_policy?: 'decline' }` | `Mandate` | 404 · 409 not a pure addition |
| C5 | POST | `/api/cards/{card_id}/policy/revoke` | — | 204 | 404 |
| C8 | POST | `/api/authorizations/{authorization_id}/resolve` | `{ decision: 'approve' \| 'decline' }` | 204 | 404 · 409 not awaiting an answer (incl. window closed) |

Unchanged from the frontend README except: C1 gains `504`, C2 gains the two `409`s.

### 1.2 Operator-only (never called by the UI, not shown to the customer)

| # | Method | Path | Request | Response |
|---|---|---|---|---|
| D1 | GET | `/api/dev/replay` | — | `ReplayStatus` (offline replay of the data pack) |
| D2 | POST | `/api/dev/replay/restart` | `{ scenario_id, card_id, speed_ms? }` | `ReplayStatus` |
| D3 | POST | `/api/dev/runs` | `{ scenario_id, card_id }` | `LiveRun` — creates a Viseca run against the card's active mandate and starts the worker |
| D4 | GET | `/api/dev/runs/{run_id}` | — | `LiveRun` — progress, counters, worker health |
| D5 | POST | `/api/dev/soft-signals` | `{ enabled: boolean }` | `{ enabled }` — chaos toggle for the small decision model |
| D6 | GET | `/api/dev/ledger/{card_id}` | — | `LedgerSnapshot` — the engine's own state, for the "reproduce this decision" view |

D3 requires an active mandate on the card (409 otherwise). D1/D2 use the same engine and
ledger as D3; only the event source differs (CSV vs Viseca long-poll).

---

## 2. Types

TypeScript is canonical for the UI (`src/api/types.ts`); Pydantic (`extra="forbid"`) for the
backend. Fields marked `NEW` are optional and may be omitted by the backend in early builds.

```ts
Customer  { customer_id, name, home_region, card_id: string|null,
            scenario_ids: string[],          // CHANGED from scenario_id: CU0001 backs two scenarios
            live: boolean }

Card      { card_id, card_type, card_purpose, status }

Account   { account_id, customer_id, account_type, account_purpose, status,
            per_transaction_limit_chf, monthly_limit_chf,   // bank limits — never on LeashMeter
            cards: Card[] }

RuleCheck { id,                              // stable id; the backend keeps the typed rule behind it
            text,                            // plain language, rendered as-is
            source: 'exact' | 'inferred',
            uncertainty: string | null,
            kind?: 'amount' | 'period' | 'merchant' | 'item' | 'terms' | 'session' | 'other' }  // NEW, for icons only

FormInput { per_order_limit_chf: number|null, period_limit_chf: number|null,
            period_days: 7|14|30|null, categories: string[],
            sellers_used_before_only: boolean,
            uncertainty_policy: 'ask' | 'decline' }

DryRunResult { sample_size, would_violate, would_fit, would_ask, insight,
               examples?: [{ occurred_at, merchant_name, billing_amount_chf,
                             outcome: 'fit'|'violate'|'ask', reason }],    // NEW, ≤3 rows
               agent_history?: { attempts: number, approved: number } }   // NEW: history rows with initiator_type 'agent'

PolicyDraft  { draft_id, card_id, instruction, checks: RuleCheck[],
               uncertainty_policy: 'ask'|'decline', open_questions: string[],
               dry_run: DryRunResult,
               compiler?: 'llm' | 'form' | 'fallback' }               // NEW: 'fallback' = LLM unavailable, rule-based parse used

Mandate      { mandate_id, card_id, instruction, checks: RuleCheck[],
               uncertainty_policy: 'ask'|'decline'|'approve', open_questions: string[],
               status: 'active'|'revoked', confirmed_at,
               usage?: MandateUsage }                                  // NEW

MandateUsage { per_order_limit_chf: number|null,                      // NEW — engine ledger, authoritative
               period_limit_chf: number|null, period_days: number|null,
               period_spent_chf: number,       // final approvals only (incl. human-approved step-ups)
               period_window_start: string,    // simulated time, ISO 8601
               pending_chf: number,            // stepped-up, awaiting the customer — not spent
               fulfilment?: { bought: number, requested: number } | null,   // single-item mandates
               confirmations?: [{ rule_text: string, merchant_name: string,  // NEW: "things you've confirmed":
                                  item_name: string }],                     // remembered yeses (ask once,
                                                                            // then remember); names untrusted
               as_of: string }                 // simulated time of the last decision

Decision {
  authorization_id, customer_id, card_id,
  decision: 'approved' | 'stopped' | 'uncertain',
  uncertain_outcome: 'pending'|'expired'|'approved'|'declined' | null,
  status: 'final' | 'pending_human',
  reason_codes: string[],
  message: string,                            // one sentence, names the number
  uncertainty: { note: string } | null,
  occurred_at: string,                        // SIMULATED time
  merchant: { merchant_id, name },            // name untrusted
  amount, currency, billing_amount_chf,
  items: [{ item_name, quantity, unit_price, currency, item_details }],   // untrusted
  injection_flag: { flagged: true, reason } | null,
  evidence: Evidence[],
  order_returnable: 'true' | 'false' | 'unknown' | 'not_applicable',
  delivery_by: string | null,
  deadline_at?: string,                       // pending_human only — REAL clock

  counterfactual?: string | null,             // NEW: "Would approve at CHF 400 or less."
  related?: { authorization_id: string,       // NEW: link to an earlier decision in this run
              relation: 'requote_of' | 'duplicate_of' | 'retry_of' | 'split_of' } | null,
  session?: { trust: 'normal' | 'elevated' | 'frozen', note: string } | null,   // NEW
  merchant_meta?: { category: string, country: string, familiar: boolean,       // NEW, trusted fields
                    prior_approvals_on_card: number, prior_approvals_other_cards: number },
  engine_version?: string,                    // NEW
  latency_ms?: number,                        // NEW: engine wall time for this decision
  explanation_source?: 'template' | 'model', // NEW: who wrote `message` (rules.md §4a, tier 3)
  resolved_by?: 'customer' | 'timeout',       // NEW: resolved step-ups only (§3.5)
  confirmable?: { rule_id: string, phrase: string } | null   // NEW: step-up decided by one `unverifiable` rule
                                              // (§3.3); phrase = its value. Approving can be remembered for the shop
}

Evidence  { rule: string,                     // which check or signal
            outcome: 'pass' | 'fail' | 'uncertain' | 'info',   // 'info' is NEW; render unknown values neutrally
            detail: string,                   // the fact, with the number
            source?: 'policy' | 'ledger' | 'history' | 'merchant_text' | 'model' }   // NEW

ReplayStatus { scenario_id, card_id, delivered, total, running: boolean, next_at: string|null }
LiveRun      { run_id, scenario_id, card_id, mandate_id, state: 'starting'|'running'|'done'|'error',
               delivered, decided, pending_human, total, worker_ok: boolean, last_error: string|null }
LedgerSnapshot { card_id, mandate_id, entries: [{ authorization_id, occurred_at, decision,
                 counted_chf, note }], period_spent_chf, frozen: boolean }
```

---

## 3. Semantics

### 3.1 Vocabulary mapping (engine ⇄ API ⇄ Viseca)

| Engine result | `Decision.decision` | `status` | `uncertain_outcome` | Viseca call |
|---|---|---|---|---|
| approve | `approved` | `final` | null | POST …/decision `approve` |
| decline | `stopped` | `final` | null | POST …/decision `decline` |
| step_up | `uncertain` | `pending_human` | `pending` | POST …/decision `step_up` |
| customer approves | `uncertain` | `final` | `approved` | POST …/resolve `approve` |
| customer declines | `uncertain` | `final` | `declined` | POST …/resolve `decline` |
| window lapses | `uncertain` | `final` | `expired` | backend posts `/resolve` `decline`, message "No answer within 120 s; nothing was approved", `resolved_by: timeout` |

A step-up **stays** `decision: 'uncertain'` after resolution; the history must keep showing
that a person was needed.

### 3.1a Consistency matrix

Valid combinations — nothing else is legal:

| `decision` | `uncertain_outcome` | `status` | `deadline_at` | Means |
| --- | --- | --- | --- | --- |
| `approved` | `null` | `final` | absent | Approved automatically |
| `stopped` | `null` | `final` | absent | Declined automatically |
| `uncertain` | `pending` | `pending_human` | **required** | Waiting on the customer |
| `uncertain` | `approved` | `final` | absent | The customer approved it |
| `uncertain` | `declined` | `final` | absent | The customer rejected it |
| `uncertain` | `expired` | `final` | absent | Nobody answered in time |

A `pending_human` row without `deadline_at` renders a step-up with no countdown and can never
expire — that is a broken state, not a degraded one.

### 3.2 Policy drafts (C1, C2)

- C1 with `instruction`: compiler pipeline = LLM extract (structured output, 8 s timeout) →
  lint → typed rules → `RuleCheck` text → dry-run against the card's history → draft.
  If the LLM fails or times out, the backend falls back to the rule-based parser and sets
  `compiler: 'fallback'`; if even that yields no amount cap, C1 returns the draft with an
  `open_questions` entry rather than failing.
- C1 with `form`: no LLM; rules built directly.
- The backend stores, per `RuleCheck.id`, the typed rule
  (`field`, `operator`, `value`, `currency?`, `scope?`, `period_days?`) in Viseca's rule
  format. **The UI only ever sees `text`; `checks` sent back in C2 are treated as accepted
  ids.** Unknown id → 422. Edited text is ignored.
- C2 re-lints the accepted subset. If the result has no per-purchase amount cap, or drops a
  check whose `source` is `exact`, C2 returns 409 with the §3.8 envelope
  `{ error: { code: 'lint_failed', message: <reason>, detail: { missing: [...] } } }`.
- On success the backend does the Viseca dance: `POST /v1/mandates` (original instruction,
  `hard_rules`, `uncertainty_policy`, `guidance` = check texts, `open_questions`) then
  `POST /v1/mandates/{draft_id}/confirm`. The returned `TM…` id is stored; our `mandate_id`
  is our own and maps to it.
- The instruction is stored **verbatim** and sent to Viseca verbatim.
- A confirmed draft replaces the card's active mandate, which is revoked (C5 semantics).
- C4 `add_checks` are ids of checks proposed by this card's drafts; their text is ignored.
  Changing a check already in force is 409 `not_pure_addition`; an unknown id is 422.

### 3.3 Field vocabulary for typed rules (engine-side, informational)

| `field` | meaning |
|---|---|
| `authorization.billing_amount_chf` | total in CHF, delivery included (never add delivery again) |
| `authorization.billing_amount_chf` + `scope: period`, `period_days: 7` | rolling window; sum of **final approvals** whose simulated timestamp ≥ current − 7×24h |
| `merchant.merchant_category` | trusted catalogue category |
| `merchant.known_shop` | `"true"` if ≥1 approved purchase by this customer at this `merchant_id` on any of their cards (history + this run's finals); customer-level per rules.md Q7. `merchant.familiar_on_card` is accepted as an alias for the same check |
| `items[].item_category` | every cart line must satisfy `in` / `not_in` |
| `items[].size_eu` | regex-extracted from `item_details`; `unknown` if absent |
| `items[].size_letter` | regex-extracted letter size (XS–XXXL, small/medium/large) from `item_details`; `unknown` if absent (C6, clothing) |
| `order.return_window_days` | regex-extracted from `item_details`; `unknown` if absent; `order_returnable == "false"` ⇒ 0 |
| `order.order_returnable` | the live string field |
| `cart.recurring` | `"true"` if any line is `subscriptions`/`membership` or text states recurring billing |
| `items[].unit_price_chf` | every cart line's `unit_price` converted to CHF (M1, M2); per-item limits |
| `items[].quantity` | every cart line's `quantity` |
| `cart.quantity` | total quantity of the requested item across all cart lines (all lines when no item is requested); "two tickets" is 1 line × 2 or 2 lines × 1 |
| `merchant.merchant_country` | trusted catalogue country, ISO 3166 alpha-2 (e.g. `"CH"`) |
| `authorization.delivery_by` | the live `delivery_by` date, compared as a date; `unknown` if `null` |
| `authorization.weekday` | purchase time in Europe/Zurich, `"mon"`..`"sun"` |
| `authorization.local_hour` | purchase time in Europe/Zurich, 0–23; time-of-day rules |
| `unverifiable` | a stated restriction no field can check (e.g. "from the official ticket seller"); always `unknown`, so C11 applies |

C2 (`scope: period`) is not evaluated with the other customer rules: it needs the run's spending memory. C2 and remembered answers are added by the pipeline via `policy.add_ledger_results`, from the LedgerView's spent and reserved amounts (M4, M5) and `confirmed_keys`, so decide and explain both see them.

Extraction from `item_details` is allowlisted regex only, produces facts, never instructions.

### 3.4 Decision lifecycle and the ledger

- One transaction in the store (docs/database.md) per decision: insert decision, consume
  live `authorization_id` (redelivery of the same id → return stored result, count nothing),
  update period spend.
- **Spend counts final approvals only.** `pending_human` contributes to `pending_chf`, not
  `period_spent_chf`. A human approval moves it across; a decline or expiry drops it.
- Rolling window uses `authorization.timestamp` (simulated). Real clock is used only for
  `deadline_at`.
- Viseca rewrites `related_authorization_id` to the **live** id of the related purchase;
  the ledger keys on live ids and keeps a live → source map for offline parity.
- Retry: same live id → idempotent. Duplicate: different id, same merchant + same cart +
  within the duplicate window → `duplicate_suspected`, `related.relation = 'duplicate_of'`.
  Re-quote: `related_authorization_id` points at a **declined** decision and the new facts
  comply → `requote_accepted`, `related.relation = 'requote_of'`, no duplicate penalty.
- C6 may return the template message first (`explanation_source: 'template'`) and the
  model rewrite on a later poll (`explanation_source: 'model'`).
- The engine reconciles `context.approved_spend_in_period_chf` from Viseca against its own
  ledger on every event, and each decision against the platform's event feed
  (`GET /v1/events?since=<cursor>`, advancing with the returned `next_cursor`) as well as
  against `context`; either mismatch is logged as an `info` evidence row.
- `Decision.evidence` may carry a W6 row (rules.md §8): the cart line, its unit price in CHF
  and the catalogue range `items.unit_price_min_chf`–`items.unit_price_max_chf`.

### 3.5 Step-up window and expiry

- `deadline_at` on a `pending_human` decision = time the `step_up` was **accepted by
  Viseca** + human window from `/v1/bootstrap` (default 120 s). Real clock.
- The worker never blocks on a pending step-up; polling continues.
- C8 after the window → 409. A GET of C6 after the window marks the decision
  `uncertain_outcome: 'expired'`, `status: 'final'` server-side (so a reload agrees). On
  expiry the backend first reads the platform's state (`GET /v1/authorizations?run_id=`):
  Viseca expires step-ups itself at the same moment, and if it already has, that result is
  recorded and nothing is posted. Still pending → `/resolve` `decline`, message "No answer
  within 120 s; nothing was approved", `resolved_by: timeout` (rules.md Q2); on a 409 the
  state is read again and recorded. Never a second `/resolve` for the same id. Not spent. A
  customer answer through C8 sets `resolved_by: 'customer'`.
- A step-up renders the **complete** purchase (all lines, delivery fee, currency, recurring
  flag, flagged text).

### 3.6 Revoke (C5)

- `DELETE /v1/mandates/{TM}` at Viseca; our mandate flips to `status: 'revoked'`, never
  deleted. Pending step-ups are shown as cancelled **only** after Viseca confirms their
  state. A revoked card can receive a new policy (new draft → new mandate).
- Session freeze (`session.trust = 'frozen'`) is engine state, not a mandate change: after a
  burst the engine step-ups the next otherwise-clean purchase once (`session_watch`;
  `session_recovered` on approval), then relaxes; a no or a timeout keeps the watch on. The
  watch is per card and carries into later live sessions (rules.md W-rule 4). The step-up card may offer a shortcut to
  the existing RevokeSheet.

### 3.7 Soft signals (Laya)

- Off by default in the offline replay; on by default in live runs if the model loaded.
  D5 toggles at runtime.
- Timeout 500 ms; on timeout/error the keyword detector runs instead. Output only ever adds
  `evidence` rows with `source: 'model'` and may raise `approve → uncertain`. It can never
  lower `stopped` or override a policy check. `engine_version` records whether the model
  was on, so a replay with it off is comparable.
- `ONEGUARD_SOFT_SIGNALS`: `off` = no soft signal; `keywords` = the A1 pattern list;
  `laya` = triggered if keywords OR Laya fire (Laya can only add, never clear a keyword hit).
- Tier-2 fact extraction and tier-3 explanation use the same provider interface as the
  compiler (OpenAI first, model-agnostic).

### 3.8 Errors

All errors: `{ error: { code: string, message: string, detail?: object } }`. Codes used:
`not_found`, `validation`, `draft_confirmed`, `lint_failed`, `not_pure_addition`,
`not_awaiting_answer`, `window_closed`, `upstream_unavailable`, `compiler_timeout`,
`internal`.
`upstream_unavailable` (503: Viseca or the database unreachable or too slow) never changes a
stored decision; the UI shows its offline state ("Nothing was approved while we were
offline"). `internal` (500) is an unexpected server error.

### 3.9 Check wording

Until `Mandate.usage` is consumed by the UI, the backend phrases limit checks exactly
(case-insensitive match in `src/lib/spend.ts`; `<amount>` may contain thousands separators
and decimals):

```
Total at or below CHF <amount> per order
Total at or below CHF <amount> across any <n> days
```

The backend emits both this wording and `usage`.

---

## 4. Reason codes (shared vocabulary)

Existing: `within_limits`, `rule_satisfied`, `per_order_limit_exceeded`,
`period_limit_exceeded`, `merchant_category_mismatch`, `unfamiliar_merchant`,
`lookalike_merchant`, `item_mismatch`, `unrequested_item`, `return_terms_unknown`,
`return_window_too_short`, `duplicate_suspected`, `injection_suspected`, `new_device_burst`,
`card_or_authority_inactive`, `unevaluable`, `customer_confirmation`.

Added: `split_order_suspected`, `requote_accepted`, `already_fulfilled`,
`recurring_charge_added`, `wrong_size`, `session_recovered`, `on_other_card`,
`foreign_currency_converted` (info), `ledger_mismatch` (info), `period_reserved_pending`,
`shop_terms_contradictory`, `rule_not_met` (a C12 rule with no specific code: per-item
price, quantity, country, weekday, delivery date), `unusual_activity` (two weak warning
signs), `session_watch` (the one ask after a burst, rules.md W-rule 4).

Development only: `stub`, emitted only while `ONEGUARD_STUBS` stubs `decide`
(`backend/oneguard/engine/stubs.py`); never in a live run.

Any new code is added here before it is emitted. The UI maps codes to labels with a
neutral fallback for unknown codes.

---

## 5. What the four judging questions map to

| Judges ask | Where it lives |
|---|---|
| What did the system permit? | `Mandate.checks` (Policy applied) + `MandateUsage` |
| What evidence did it consider? | `Decision.evidence[]` with `source` |
| Why did it act? | `Decision.message`, `reason_codes`, `counterfactual`, `injection_flag` |
| How did the customer retain control? | C8 resolve, C5 revoke, `session` note, `uncertainty_policy` |

---

## 6. Frontend change list (additive)

1. `Customer.scenario_id` → `scenario_ids: string[]`.
2. `Decision.related` — one link row in DecisionDetail "Related decisions".
3. `Decision.counterfactual` — one line under the message in DecisionDetail.
4. `Mandate.usage` — `lib/spend.ts` prefers it when present; keep client math as mock fallback; ensure human-approved step-ups count as spend.
5. `Evidence.outcome: 'info'` — neutral styling; unknown values fall back to neutral.
6. Optional: `Decision.session` banner on DecisionDetail when trust ≠ normal; a "Revoke policy" shortcut on the Approvals card.
7. Fixtures: add the new fields to `build_decisions_fixture.py` / `build_policy_fixture.py` so mock mode matches.
8. `Decision.explanation_source` and `Decision.resolved_by` in `types.ts`; `mergeDecisions.sameDecision` also compares `explanation_source` and `counterfactual` so a tier-3 rewrite re-renders.
9. Policy screen renders DryRunResult.examples and dry_run.agent_history as one line
10. PolicyDraft.compiler == 'fallback' shown as a banner; Decision.explanation_source shown as a subtle tag
11. Optional: `Mandate.usage.confirmations` as a "Things you've confirmed" list on the policy screen (names rendered as plain text)
12. Optional: `Decision.confirmable` - on a step-up, "Approve, and treat <shop> as <phrase> from now on"; absent or null means the ordinary approve button

No endpoint changes. No screen removals. Tighten UI stays dormant.

---

## Appendix A. Backend checklist

Merged from the former frontend/API-CONTRACT.md §7 (section references point at this file).
`backend/tests/test_api_contract.py` must cover every line of it.

Shape:

- [ ] All nine endpoints on `/api`, served from the same origin as the app
- [ ] Static file mount registered **after** every API route
- [ ] Envelope per endpoint exactly as §1.0 (wrapped vs. bare vs. `204`)
- [ ] Every documented key present; nullable keys present with explicit `null`
- [ ] `cards`, `items`, `evidence`, `reason_codes`, `open_questions` are arrays, never `null`

Correctness:

- [ ] `decision` / `uncertain_outcome` / `status` only ever in a combination from §3.1a's matrix
- [ ] `deadline_at` present on every `pending_human` row, stable across polls
- [ ] Every timestamp ISO-8601 UTC with `Z`, fixed width, no mixed offsets
- [ ] `occurred_at` is simulated time; `deadline_at` is the real clock
- [ ] `evidence` non-empty on every decision, `message` names the actual number
- [ ] Limit checks worded exactly as §3.9, or the meter shows nothing
- [ ] `amount` includes delivery; `billing_amount_chf` present on every row
- [ ] `order_returnable` is one of the four strings; `unknown` / `not_applicable` / `null` distinct
- [ ] C6 returns the full history for that customer only, on every poll
- [ ] `authorization_id` unique and stable

Safety:

- [ ] Viseca key server-side only; nothing secret reachable from `/api` responses
- [ ] `injection_flag` set by the backend; `null` when clean, never omitted
- [ ] A resolve for a closed window is refused, not recorded
- [ ] Lapsed step-ups closed server-side so expiry survives a reload
- [ ] Revoke touches the policy only, never a purchase in flight
- [ ] Tighten rejects anything that is not a pure addition
- [ ] No endpoint ever returns `2xx` for work that did not happen

Also carried over from the frontend contract:

- [ ] JSON request and response bodies (`application/json`, UTF-8); base URL `/api` is relative
- [ ] `not_applicable` is not uncertainty and never causes an escalation
- [ ] Merchants joined by `merchant_id`, never by name
- [ ] `amount`/`currency` and `billing_amount_chf` both sent, even when the currency is CHF
- [ ] `deadline_at` reflects the platform's real window; the UI's 120 s constant is not assumed
- [ ] Every call bounded server-side: a fast failure, never a hang

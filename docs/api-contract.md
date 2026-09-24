# OneGuard — API contract (frontend ⇄ backend)

Version 1.0 · 24 Sep 2026 · replaces `.claude/contracts/api-contract.md`

This contract adopts the frontend's existing shapes **unchanged** and only adds fields.
Every addition is marked `NEW` and is optional on the wire, so the current UI keeps
working before any of it is rendered. If this file and `backend/app/domain.py` disagree,
`domain.py` wins and this file gets fixed in the same commit.

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
                             outcome: 'fit'|'violate'|'ask', reason }] }   // NEW, ≤3 rows

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
  latency_ms?: number                         // NEW: engine wall time for this decision
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
| window lapses | `uncertain` | `final` | `expired` | none — never invent a human answer |

A step-up **stays** `decision: 'uncertain'` after resolution; the history must keep showing
that a person was needed.

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
  check whose `source` is `exact`, C2 returns `409 { error: 'lint_failed', reason, missing: [...] }`.
- On success the backend does the Viseca dance: `POST /v1/mandates` (original instruction,
  `hard_rules`, `uncertainty_policy`, `guidance` = check texts, `open_questions`) then
  `POST /v1/mandates/{draft_id}/confirm`. The returned `TM…` id is stored; our `mandate_id`
  is our own and maps to it.
- The instruction is stored **verbatim** and sent to Viseca verbatim.

### 3.3 Field vocabulary for typed rules (engine-side, informational)

| `field` | meaning |
|---|---|
| `authorization.billing_amount_chf` | total in CHF, delivery included (never add delivery again) |
| `authorization.billing_amount_chf` + `scope: period`, `period_days: 7` | rolling window; sum of **final approvals** whose simulated timestamp ≥ current − 7×24h |
| `merchant.merchant_category` | trusted catalogue category |
| `merchant.familiar_on_card` | `"true"` if ≥1 approved purchase on this card at this `merchant_id` (history + this run's finals) |
| `items[].item_category` | every cart line must satisfy `in` / `not_in` |
| `items[].size_eu` | regex-extracted from `item_details`; `unknown` if absent |
| `order.return_window_days` | regex-extracted from `item_details`; `unknown` if absent; `order_returnable == "false"` ⇒ 0 |
| `order.order_returnable` | the live string field |
| `cart.recurring` | `"true"` if any line is `subscriptions`/`membership` or text states recurring billing |
| `items[].unit_price_chf` | every cart line's `unit_price` converted to CHF (M1, M2); per-item limits |
| `items[].quantity` | every cart line's `quantity` |
| `merchant.merchant_country` | trusted catalogue country, ISO 3166 alpha-2 (e.g. `"CH"`) |
| `authorization.delivery_by` | the live `delivery_by` date, compared as a date; `unknown` if `null` |
| `authorization.weekday` | purchase time in Europe/Zurich, `"mon"`..`"sun"` |
| `authorization.local_hour` | purchase time in Europe/Zurich, 0–23; time-of-day rules |

Extraction from `item_details` is allowlisted regex only, produces facts, never instructions.

### 3.4 Decision lifecycle and the ledger

- One SQLite transaction per decision: insert decision, consume live `authorization_id`
  (redelivery of the same id → return stored result, count nothing), update period spend.
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
- The engine reconciles `context.approved_spend_in_period_chf` from Viseca against its own
  ledger on every event and logs a mismatch as an `info` evidence row.

### 3.5 Step-up window and expiry

- `deadline_at` on a `pending_human` decision = time the `step_up` was **accepted by
  Viseca** + human window from `/v1/bootstrap` (default 120 s). Real clock.
- The worker never blocks on a pending step-up; polling continues.
- C8 after the window → 409. A GET of C6 after the window marks the decision
  `uncertain_outcome: 'expired'`, `status: 'final'` server-side (so a reload agrees). The
  backend does **not** call `/resolve` for an expired window.
- A step-up renders the **complete** purchase (all lines, delivery fee, currency, recurring
  flag, flagged text). C8 is bound to a hash of that rendering; if the pending
  authorization's facts have changed since (checked against `GET /v1/authorizations`), the
  backend returns 409 and the UI shows the refreshed decision.

### 3.6 Revoke (C5)

- `DELETE /v1/mandates/{TM}` at Viseca; our mandate flips to `status: 'revoked'`, never
  deleted. Pending step-ups are shown as cancelled **only** after Viseca confirms their
  state. A revoked card can receive a new policy (new draft → new mandate).
- Session freeze (`session.trust = 'frozen'`) is engine state, not a mandate change: after a
  burst the engine step-ups the next otherwise-clean purchase once
  (`session_recovered` on approval), then relaxes. The step-up card may offer a shortcut to
  the existing RevokeSheet.

### 3.7 Soft signals (Laya)

- Off by default in the offline replay; on by default in live runs if the model loaded.
  D5 toggles at runtime.
- Timeout 500 ms; on timeout/error the keyword detector runs instead. Output only ever adds
  `evidence` rows with `source: 'model'` and may raise `approve → uncertain`. It can never
  lower `stopped` or override a policy check. `engine_version` records whether the model
  was on, so a replay with it off is comparable.

### 3.8 Errors

All errors: `{ error: { code: string, message: string, detail?: object } }`. Codes used:
`not_found`, `validation`, `draft_confirmed`, `lint_failed`, `not_pure_addition`,
`not_awaiting_answer`, `window_closed`, `upstream_unavailable`, `compiler_timeout`.
`upstream_unavailable` (Viseca down) never changes a stored decision; the UI shows its
offline state ("Nothing was approved while we were offline").

---

## 4. Reason codes (shared vocabulary)

Existing: `within_limits`, `rule_satisfied`, `per_order_limit_exceeded`,
`period_limit_exceeded`, `merchant_category_mismatch`, `unfamiliar_merchant`,
`lookalike_merchant`, `item_mismatch`, `unrequested_item`, `return_terms_unknown`,
`return_window_too_short`, `duplicate_suspected`, `injection_suspected`, `new_device_burst`,
`card_or_authority_inactive`, `unevaluable`, `customer_confirmation`.

Added: `split_order_suspected`, `requote_accepted`, `already_fulfilled`,
`recurring_charge_added`, `wrong_size`, `session_recovered`, `on_other_card`,
`foreign_currency_converted` (info), `ledger_mismatch` (info).

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

No endpoint changes. No screen removals. Tighten UI stays dormant.

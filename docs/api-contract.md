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
| C6 | GET | `/api/customers/{customer_id}/decisions[?operator=1]` | — | `{ decisions: Decision[] }` newest first; operator-only evidence only with `?operator=1` (§3.4) | 404 |
| C1 | POST | `/api/cards/{card_id}/policy-drafts` | `{ instruction }` **or** `{ form: FormInput }` | `PolicyDraft` | 404 card · 422 neither/both, or an `instruction` over 1,000 characters · 429 `rate_limited`: more than 10 drafts in a minute for the card's customer · 504 compiler timeout |
| C2 | POST | `/api/policy-drafts/{draft_id}/confirm` | `{ checks: RuleCheck[], uncertainty_policy, open_questions }` | `Mandate` | 404 draft · 401 not signed by a device on the draft's card (§3.10) · 409 draft already confirmed · 422 unknown check id · 409 `lint_failed` (see §3.2) |
| C3 | GET | `/api/cards/{card_id}/policy` | — | `{ mandate: Mandate \| null }` | 404 card |
| C4 | POST | `/api/cards/{card_id}/policy/tighten` | `{ add_checks: RuleCheck[], uncertainty_policy?: 'decline' }` | `Mandate` | 404 · 401 (§3.10) · 409 not a pure addition |
| C5 | POST | `/api/cards/{card_id}/policy/revoke` | — | 204 | 404 · 401 (§3.10) |
| C8 | POST | `/api/authorizations/{authorization_id}/resolve` | `{ decision: 'approve' \| 'decline' }` | 204 | 404 · 401 not signed by a device on the purchase's card (§3.10) · 409 not awaiting an answer (incl. window closed) |

Unchanged from the frontend README except: C1 gains `504` and (NEW) the 1,000-character `422` and `429`, C2 gains the two `409`s, and C2, C4, C5
and C8 are device-bound writes (§3.10, NEW): refused with `401` unless signed by a device enrolled
on the card, before anything is read for the change or applied.

### 1.3 Passport, receipts and devices (NEW, docs/passport.md)

| # | Method | Path | Request | Response | Errors |
|---|---|---|---|---|---|
| P1 | GET | `/api/passport/keys` | — | `{ keys: PublicKey[] }` the active key first | |
| P2 | GET | `/api/cards/{card_id}/passport` | — | `Passport`: the latest version of the card's current policy's passport, with `versions` | 404 card, or no passport (no policy confirmed yet) |
| P3 | GET | `/api/cards/{card_id}/passport/qr.svg` | — | `image/svg+xml`: a QR code of `<ONEGUARD_PUBLIC_URL>/verify?passport=<passport_id>&v=<version>` (default `https://oneguard.fly.dev`) | 404 as P2 |
| P4 | GET | `/api/authorizations/{authorization_id}/receipt` | — | `Receipt` (signed on the spot when the background sweep has not reached the decision yet) | 404 |
| P5 | POST | `/api/verify` | `{ document, signature, key_id }` **or** `{ passport_id, version? }` **or** `{ receipt_id }` | `VerifyResult` | 404 unknown stored id · 422 not exactly one form |
| P6 | GET | `/api/cards/{card_id}/devices` | — | `{ devices: Device[] }` enrolled first, then pending, then removed; each enrolled one with its `role` | 404 card |
| P7 | POST | `/api/cards/{card_id}/devices` | `{ public_key_jwk, label? }` (P-256 public JWK) | `{ device_id, status }`: `enrolled` for the card's first device (its controller), else `pending`; the same key again returns the same device | 404 card · 422 not a P-256 key |
| P8 | POST | `/api/cards/{card_id}/devices/{device_id}/approve` | — (signed by the controller, §3.10) | `Device` (enrolled, role `approved`, `enrolled_by_device_id` the signer); a new passport version | 401 · 403 `not_controller` · 404 · 409 `device_state` (not pending) |
| P9 | POST | `/api/cards/{card_id}/devices/{device_id}/remove` | — (signed by the controller, §3.10) | `Device` (removed); a new passport version when it was enrolled | 401 · 403 `not_controller` · 404 · 409 `last_device` (the card's only enrolled device) · 409 `device_state` (already removed, or the controller itself while another device is enrolled: transfer first) |
| P10 | POST | `/api/cards/{card_id}/devices/{device_id}/transfer` | — (signed by the controller, §3.10) | `Device` (role `controller`); the signer stays enrolled as `approved`; a new passport version (reason `controller`) | 401 · 403 `not_controller` · 404 · 409 `device_state` (not enrolled, or already the controller) |
| — | GET | `/verify` | query `passport` + `v`, or `receipt` | the app's HTML; the page calls P5 (where the P3 QR code points) | |

Operator-only: `POST /api/dev/devices/reset/{card_id}` → `{ card_id, removed }`: every device on
the card removed, so the next one enrols as the card's first (issuer-side recovery in a real
rollout). `403 forbidden` when `ONEGUARD_ENV=prod`. Behind the operator gate like every
`/api/dev/*` route (§1.2).

### 1.2 Operator-only (never called by the UI, not shown to the customer)

**Operator gate (NEW).** When `ONEGUARD_ENV=prod` (the image default) every `/api/dev/*`
route needs the header `X-OneGuard-Operator` equal to the server's `ONEGUARD_OPERATOR_TOKEN`
(a Fly secret), compared in constant time, before anything else runs: missing or wrong is
`401 operator_required`, and a production server with no token set answers every
`/api/dev/*` call `503 operator_unconfigured` rather than serving them open. Off production
the check is skipped. The token is never logged or echoed. `make demo-live`,
`make demo-offline` and `frontend/scripts/check_policy_refresh.py` send it from
`ONEGUARD_OPERATOR_TOKEN` in their own environment; the console (`/ops`) asks for it once
(§6 item 23). D9 (`/api/scenarios`) is outside `/api/dev` and stays open.

| # | Method | Path | Request | Response |
|---|---|---|---|---|
| D1 | GET | `/api/dev/replay` | — | `ReplayStatus` (offline replay of the data pack) |
| D2 | POST | `/api/dev/replay/restart` | `{ scenario_id, card_id, speed_ms? }` | `ReplayStatus` — a pack scenario replays the pack's purchases; any other catalogued scenario replays from record (below) |
| D3 | POST | `/api/dev/runs` | `{ scenario_id, card_id, force?: boolean }` | `LiveRun` — creates a Viseca run against the card's active mandate, followed by the worker; names the run card's holder |
| D4 | GET | `/api/dev/runs/{run_id}` | — | `LiveRun` — progress, counters, worker health |
| D5 | POST | `/api/dev/soft-signals` | `{ enabled: boolean }` | `{ enabled }` — chaos toggle for the small decision model |
| D5 | GET | `/api/dev/soft-signals` | - | `{ live: boolean, replay: boolean }` - whether the models run now for live runs and for the offline replay; the two differ until an operator sets D5 (§3.7). Reads only |
| D6 | GET | `/api/dev/ledger/{card_id}` | — | `LedgerSnapshot` — the engine's own state, for the "reproduce this decision" view |
| D7 | GET | `/api/dev/runs/current` | — | `LiveRun` or `ReplayStatus` — the newest run (live or replay, by the real time it started) with the counters D4 / D1 show; 404 when none. Starts nothing |
| D8 | GET | `/api/dev/scenarios` | — | `{ scenarios: Scenario[] }` — every scenario in the store's catalogue, whether the platform serves it now, the customer and card it runs on when known, and a run of it still in progress. Reads only |
| D9 | GET | `/api/scenarios` | - | `{ scenarios: ScenarioSummary[] }`: the operator console's picker (`/ops`): every scenario in the store's catalogue (the pack's and the served ones) with its purchase count and the customer and card it runs on (the stored platform binding, else the pack's; null when nothing names one yet), grouped by customer (by name), unnamed ones last. Reads the store only: no platform call |

D3 requires an active mandate on the card (409 otherwise). Before the run it reads that mandate at the platform
(`GET /v1/mandates/{id}`): the sandbox keeps one active mandate per team, so a policy confirmed later (another card, a
later judging run) supersedes ours there while the customer's policy stays active here. When the platform reports it
anything but `active` (`superseded`, `revoked`, `expired`) or does not know it (404), D3 creates and confirms the same
policy there again, as C2 did (its instruction, a form policy's checks as sentences, its checks as `hard_rules` and
`guidance`), stores the new platform id on the same policy (its `mandate_id`, checks and `status` unchanged), re-issues
the card's passport naming it (docs/passport.md, reason `platform`), then starts the run under it. `LiveRun.platform_mandate`
says what D3 found and did; the console's run header shows "platform mandate re-registered". A refused re-registration
starts nothing: 503 `upstream_unavailable` with the platform's `platform_status` and `platform_code`, as any refused
platform call, and the refusal is kept in `worker_events` (`/healthz` `last_refusal`, docs/database.md §2). A read that
fails otherwise starts the run as before (`status_before: null`). During a run, an event whose `mandate` snapshot says
`superseded` (or any status but `active`) is logged and recorded once per mandate and the run decides on under the local
policy: the snapshot's status never decides a purchase. While `ONEGUARD_ALLOW_RUNS=false` D3 starts nothing and
answers 409 `runs_disabled` (unset: runs allowed); `make demo-live` refuses the same way. While the worker still follows
an unfinished run D3 answers 409 `run_active` (detail `{ run_id, scenario_id }`) before anything else: one run at a
time. Also 409 `run_active` when the scenario has a run in progress anywhere: a live run the store last saw starting or
running (unless the platform's `GET /v1/scenario-runs/{id}` says it is over), or a run with a purchase still open at
the platform (`GET /v1/authorizations` status `awaiting_decision` or `pending_step_up`), whoever started it; the
message and detail name that run. `force: true` skips both checks and starts the run anyway. D1/D2 use the same engine and ledger as D3; only the event source differs (CSV vs Viseca long-poll).
D3 accepts any scenario in the store's `scenario_catalogue`, which the worker syncs from Viseca's
`/v1/reference-data` at start and again when the served pack changes (docs/judging-pack.md, architecture.md Runtime):
404 for an unknown scenario or card, 422 when the scenario's card is known and is another. D3 and D8 first have the
worker re-read `/v1/bootstrap` (human window, decision deadline, long-poll wait); a new `pack_version` syncs the
reference data before D8 lists the catalogue, so `make demo-live` compiles the instruction the platform serves now. D2 replays
the local pack's purchases for a scenario the pack has; any other scenario in the catalogue it **replays from record**: the
stored events (`events_raw`) of the scenario's newest live run with stored events, in the order they arrived, verbatim but
for fresh live ids (`related_authorization_id` rewritten through the same map), the replay's mandate id and an empty
`context` that the replay's own decisions fill, through the current engine and the card's policy (as for a pack replay).
It runs on that run's card (422 for another) and names its holder, and its `ReplayStatus` says `source: 'record'` with
the platform's `record_run_id` and `record_started_at`. Nothing is fetched from or posted to the platform: its step-ups
are closed locally, by C8 or by expiry (§3.5), like any replay's. A scenario with no stored live events answers 404 with
"has not run yet"; an unknown one 404. D2 decides by the card's own policy, its words and checks:
the active one (`policy_source: 'card'`), else the last one, revoked, under which every purchase declines at step 1
(`policy_source: 'revoked'`). Only a card that never had a policy has the scenario's instruction compiled for that
replay alone, stored as no mandate (`policy_source: 'scenario'`, `mandate_id: 'replay-<scenario_id>'`).

**Scenario bindings.** The served catalogue names no card. The platform names one in the `/v1/bootstrap` `profile`
(one scenario), in every run's `fixture_profiles` and in every authorization; the worker stores each sighting
(`scenario_profiles`, docs/database.md) at start (bootstrap, and one `GET /v1/authorizations`), from D3's run reply,
from run progress, from a run's first event and from the event feed. A scenario's binding is the stored one, else the
local pack's. C12 `scenario_ids` lists every scenario bound to the customer's cards; `live` is true when one of them is
served now (the scenario ids the worker read at its last start), or, for a store that never reached the platform,
when any is bound (the offline replay). C12 `card_id` is the card of the customer's newest live scenario, else newest
scenario, else the card of an active policy. D3's reply (`LiveRun`) names the card the run's fixture profile uses and
its holder (`customer_id`, `customer_name`); when that card is not the one D3 was called with (a scenario never run
before), the policy moves there: a copy of the mandate (same Viseca mandate) becomes that card's active policy and the
original is marked revoked (docs/decisions.md). D4 and D7 name the holder too.

**`make demo-live SCEN=<id>`** never decides: it drives the server at `ONEGUARD_API_URL` (default
`https://oneguard.fly.dev`): D8 → D7 (an unfinished newest run, or the scenario's `active_run_id`: that run is named,
exit 1, nothing changed; `--force` / `make demo-live FORCE=1` skips this and sends D3 `force: true`) → C1 on the scenario's card (unknown:
`--card`, else the bootstrap profile's) → C2 → D3, prints `Sign in as <name> (<customer_id>, card <card_id>)` from
D3's reply, then follows D4 and the customer's C6 read-only. **`make demo-offline SCEN=<id>`** calls D2 on the same
server. Both probe `/healthz` first: when no OneGuard server answers there they say so and exit 1, and start nothing
(no run, no replay, no worker of their own).

---

## 2. Types

TypeScript is canonical for the UI (`src/api/types.ts`); Pydantic (`extra="forbid"`) for the
backend. Fields marked `NEW` are optional and may be omitted by the backend in early builds.

```ts
Customer  { customer_id, name, home_region, card_id: string|null,
            scenario_ids: string[],          // CHANGED from scenario_id: CU0001 backs two scenarios;
                                             // every scenario bound to their cards (§1.2 Scenario bindings)
            live: boolean }                  // one of them is served now

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
               agent_history?: { attempts: number, approved: number } }   // NEW: history rows with initiator_type 'agent',
                                              // customer-level (all the customer's cards); the rest of the dry run is card-scoped
                                              // a known-shop check (merchant.known_shop / familiar_on_card, or requires_known_shop
                                              // alone) makes a purchase at a shop the card had not bought from before 'ask', never
                                              // 'violate': the same for a form and an instruction draft

PolicyDraft  { draft_id, card_id, instruction, checks: RuleCheck[],   // instruction: the C1 text verbatim, or exactly
                                              // "Built from the form" for a form draft; never the check texts
               uncertainty_policy: 'ask'|'decline', open_questions: string[],  // no checks read: the first entry is
                                              // "I couldn't read a spending limit or item type - try 'groceries, max CHF 120 per order'"
               dry_run: DryRunResult,
               compiler?: 'llm' | 'form' | 'fallback' }               // NEW: 'fallback' = LLM unavailable, rule-based parse used

Mandate      { mandate_id, card_id, instruction, checks: RuleCheck[],   // instruction: as its draft's (C4 keeps it)
               uncertainty_policy: 'ask'|'decline'|'approve', open_questions: string[],
               status: 'active'|'revoked', confirmed_at,
               usage?: MandateUsage,                                   // NEW
               passport?: { passport_id, version: number, issued_at,   // NEW: its passport's latest version
                            devices_count: number } }                 // (absent until one is issued)

MandateUsage { per_order_limit_chf: number|null,                      // NEW — engine ledger, authoritative
               period_limit_chf: number|null, period_days: number|null,
               period_spent_chf: number,       // final approvals only (incl. human-approved step-ups)
               period_window_start: string,    // simulated time, ISO 8601
               pending_chf: number,            // stepped-up, awaiting the customer — not spent
               fulfilment?: { bought: number, requested: number } | null,   // single-item mandates (A8):
                                                                            // final approvals of the requested
                                                                            // item in the latest run, of 1
               confirmations?: [{ rule_text: string, merchant_name: string,  // NEW: "things you've confirmed":
                                  item_name: string }],                     // remembered yeses (ask once,
                                                                            // then remember); names untrusted
               as_of: string }                 // simulated time of the last decision
                                               // Counted from the card's newest run, whichever mandate
                                               // decided it (a D3 move re-ids the policy mid-run; a new
                                               // policy shows the card's last run until its own); no
                                               // period limit: the window is the whole run

Decision {
  authorization_id, customer_id, card_id,
  decision: 'approved' | 'stopped' | 'uncertain',
  uncertain_outcome: 'pending'|'expired'|'approved'|'declined' | null,
  status: 'final' | 'pending_human',
  reason_codes: string[],
  message: string,                            // "{Outcome} CHF {amount}: {clause}." one clause (rules.md §9); never the counterfactual
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

  counterfactual?: string | null,             // NEW: "Would approve at CHF 400.00 or less." on its own, not in `message`
  related?: { authorization_id: string,       // NEW: link to an earlier decision in this run
              relation: 'requote_of' | 'duplicate_of' | 'retry_of' | 'split_of' } | null,
  session?: { trust: 'normal' | 'elevated' | 'frozen', note: string } | null,   // NEW
  merchant_meta?: { category: string, country: string, familiar: boolean,       // NEW, trusted fields
                    prior_approvals_on_card: number, prior_approvals_other_cards: number },
  engine_version?: string,                    // NEW
  latency_ms?: number,                        // NEW: engine wall time for this decision
  explanation_source?: 'template' | 'model', // NEW: who wrote `message` (rules.md §4a, tier 3); UI tag: template →
                                              // "Explained by OneGuard", model → "Wording refined by AI · decision made by your rules"
  resolved_by?: 'customer' | 'timeout',       // NEW: resolved step-ups only (§3.5)
  confirmable?: { rule_id: string, phrase: string } | null,  // NEW: step-up decided by one `unverifiable` rule
                                              // (§3.3); phrase = its value. Approving can be remembered for the shop
  run_id?: string,                            // NEW: the run this decision belongs to (decisions.run_id); C6 sends it
  run_started_at?: string,                    // NEW: that run's start, REAL clock (runs.started_at); C6 sends it
                                              // when the run has a `runs` row. The UI lists a card's newest run
  policy_applied?: { mandate_id: string,      // NEW: the policy this decision was checked against
                     source: 'confirmed' | 'platform',   // platform = the Viseca mandate's own rules: decisions made before
                                                         // `no_active_policy`; none since (§4). Null on a no_active_policy
                                                         // decline with no policy, and never another card's policy
                     checks: RuleCheck[] } | null,       // what decided, whatever the card's policy is now
  would_approve_if: Bound[] | null,           // NEW: the counterfactual structured, declines only (null otherwise
                                              // and on decisions made before it existed); what the agent is told
  receipt_id?: string,                        // NEW: the signed receipt of this decision (P4)
}

Bound       { field: string, operator: string, value: number|string|string[],   // NEW (docs/passport.md)
              scope?: 'period', period_days?: number }       // e.g. billing_amount_chf <= 400
          | { remove_items: string[] }                       // the cart lines (item ids) to drop
          | { requires: string }                             // known_shop, requested_item, clean_merchant_text,
                                                             // active_policy, unanswered_declined, ...

Evidence  { rule: string,                     // which check or signal
            outcome: 'pass' | 'fail' | 'uncertain' | 'info',   // 'info' is NEW; render unknown values neutrally
            detail: string,                   // the fact, with the number
            source?: 'policy' | 'ledger' | 'history' | 'merchant_text' | 'model' }   // NEW

ReplayStatus { scenario_id, card_id, delivered, total, running: boolean, next_at: string|null,
               ledger_run_id?: string,          // NEW: the run_id this replay's C6 decisions carry
               started_at?: string,             // NEW: when it started, REAL clock
               decided?: number,                // NEW: purchases decided so far
               customer_id?: string, customer_name?: string,          // NEW: who holds card_id
               mandate_id?: string,             // NEW: the policy the replay decides by
               policy_source?: 'card' | 'revoked' | 'scenario',  // NEW: the card's active policy, its last
                                                // one revoked (every purchase declines), or (a card that never
                                                // had one) the scenario's instruction compiled for this replay only
               source?: 'pack'|'record',        // NEW: the pack's purchases, or a live run's stored events (D2)
               record_run_id?: string,          // NEW: record only: that live run, the platform's run id
               record_started_at?: string }     // NEW: record only: when that run started, REAL clock
LiveRun      { run_id, scenario_id, card_id, mandate_id, state: 'starting'|'running'|'done'|'error',
               delivered, decided, pending_human, total, worker_ok: boolean, last_error: string|null,
               customer_id?: string, customer_name?: string,          // NEW: who holds card_id
               ledger_run_id?: string,          // NEW: the run_id this run's C6 decisions carry (run_id is the platform's)
               started_at?: string,             // NEW: when it started, REAL clock
               platform_mandate?: {             // NEW: D3's check of the policy's mandate at the platform (§1.2)
                 status_before: string | null,  //   'active', 'superseded', 'revoked', ..., 'missing' (404); null: unread
                 reregistered: boolean,         //   the same policy was registered at the platform again
                 viseca_mandate_id: string,     //   the platform mandate the run started under
                 previous_viseca_mandate_id?: string } }  // re-registered only: the one it replaced
                                                // (a D7 body with run_id is a LiveRun; without, a ReplayStatus)
Scenario     { scenario_id, scenario_name, cardholder_instruction,    // NEW (D8)
               served: boolean,                                       // the platform serves it now
               profile: { customer_id, name, card_id, profile_id: string|null,
                          source: 'pack'|'bootstrap'|'run'|'authorization' } | null,   // null: not run yet
               active_run_id: string | null }                         // a run of it in progress (D3's run_active)
ScenarioSummary { scenario_id, name, event_count: number,             // NEW (D9)
                  instruction,                                         // the cardholder instruction, verbatim
                  customer_id: string|null, customer_name: string|null, card_id: string|null,
                  replay_source: 'pack'|'record'|null }                // what D2 replays; null: not run yet
LedgerSnapshot { card_id, mandate_id, entries: [{ authorization_id, occurred_at, decision,
                 counted_chf, note }], period_spent_chf, frozen: boolean }

// NEW: passport, receipts, devices (§1.3, docs/passport.md). `document` is the signed body,
// byte for byte what canonical JSON of it signs; the UI renders fields from it as plain text.
PublicKey    { key_id, algorithm: 'ed25519', public_key_pem, active: boolean }
Passport     { passport_id, version, document, signature, key_id,
               versions: [{ version, issued_at, reason }] }   // reason: confirmed, backfill, tightened,
                                                             // devices, controller, confirmation, revoked, updated
Receipt      { receipt_id, document, signature, key_id,
               history: [{ document, signature, key_id, signed_at }] }   // earlier signatures (before the answer)
Device       { device_id, card_id, label,                    // label: the customer's own text
               status: 'pending'|'enrolled'|'removed', enrolled_at: string|null,
               enrolled_by_device_id: string|null, removed_at: string|null, last_seen_at,
               role: 'controller'|'approved'|null }           // enrolled only: the one controller, or approved
VerifyResult { valid: boolean, document_type: 'passport'|'receipt'|null, key_id: string|null,
               issued_at: string|null,                       // passport issued_at; receipt decided_at
               reason: string, document?: object|null,       // the document checked (the stored one for an id)
               current?: boolean|null }                      // passport: latest version and not revoked
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
| window lapses | `uncertain` | `final` | `expired` | backend posts `/resolve` `decline`, message "No answer within 120 s; nothing was approved", `resolved_by: timeout`; the Decision's `message` becomes "Expired: no answer within 120 s; nothing was approved." (the configured window), `counterfactual` null |

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
  `open_questions` entry rather than failing. If no check at all was read (e.g. "buy
  something nice"), that entry is "I couldn't read a spending limit or item type - try
  'groceries, max CHF 120 per order'", first, in place of the no-amount question.
- C1 refuses an `instruction` longer than 1,000 characters (`422 validation`,
  `detail: { max_chars, chars }`) before anything compiles it, and more than 10 drafts
  (instruction or form) for one customer in any 60 s (`429 rate_limited`), counted before the
  compiler runs, so a draft that later times out still counts. The window is real time, kept
  in the server process's memory: the app runs on one machine, and a restart only forgets
  it (the customer may draft again sooner).
- C1 with `form`: no LLM; rules built directly. The form has no words of the customer's, so
  the draft's (and the mandate's) `instruction` is exactly "Built from the form".
- The backend stores, per `RuleCheck.id`, the typed rule
  (`field`, `operator`, `value`, `currency?`, `scope?`, `period_days?`) in Viseca's rule
  format. **The UI only ever sees `text`; `checks` sent back in C2 are treated as accepted
  ids.** Unknown id → 422. Edited text is ignored.
- Two checks have no typed rule behind them; they show Policy flags so the customer sees
  them before confirming: `requested_item` ("Only the item you asked for: <item>", C5) and
  `nothing_extra` ("Nothing added that you didn't ask for", C10), both `source: 'exact'`,
  `kind: 'item'`, after the rule checks. The engine reads the flags, never these checks.
  C2 accepts their ids and, like any exact check, refuses a draft that drops one
  (`missing` names the id); C4 treats one already in force as nothing to add.
- C2 refuses a draft with no checks before anything else: 409 `lint_failed`, message
  "Not confirmed: no restriction could be read.", `detail: { missing: ['per_order_limit'] }`.
  Nothing is sent to Viseca and no mandate is stored.
- C2 re-lints the accepted subset. If the result has no per-purchase amount cap, or drops a
  check whose `source` is `exact`, C2 returns 409 with the §3.8 envelope
  `{ error: { code: 'lint_failed', message: <reason>, detail: { missing: [...] } } }`.
- On success the backend does the Viseca dance: `POST /v1/mandates` (original instruction,
  `hard_rules`, `uncertainty_policy`, `guidance` = check texts, `open_questions`) then
  `POST /v1/mandates/{draft_id}/confirm`. The returned `TM…` id is stored; our `mandate_id`
  is our own and maps to it.
- The instruction is stored **verbatim** and sent to Viseca verbatim; C1, C2, C3 and C4 serve
  it unchanged. A form policy stores and serves "Built from the form" and sends Viseca its
  accepted checks as sentences ("Total at or below CHF 20 per order. Ask me when
  uncertain."), since the platform wants text.
- C2 does not pass the real clock; date phrases resolve from the card's simulated date.
- A confirmed draft replaces the card's active mandate, which is revoked (C5 semantics).
- C4 `add_checks` are ids of checks proposed by this card's drafts; their text is ignored.
  Changing a check already in force is 409 `not_pure_addition`; an unknown id is 422.

### 3.3 Field vocabulary for typed rules (engine-side, informational)

| `field` | meaning |
|---|---|
| `authorization.billing_amount_chf` | total in CHF, delivery included (never add delivery again) |
| `authorization.billing_amount_chf` with `value: "last_price_at_shop"` (a reference, not a number; `currency: CHF`, `scope: purchase`) | the total against the customer's last approved total at this purchase's `merchant_id`: history's last approved price there, replaced by this run's latest final approval there (declines and pending step-ups set no price; a step-up the customer approved does). No earlier payment at the shop: `unknown`, never a pass. "If a price changes, ask me" for several subscriptions is `=` with `on_fail: ask`: evidence "CHF 14.90 at Streamly; last time it was CHF 12.90", counterfactual "Would approve at CHF 12.90, the price last time". Not a per-order cap (lint still asks for one). `LedgerView.last_price_chf_by_merchant` carries the prices |
| `authorization.billing_amount_chf` + `scope: period`, `period_days: 7` | rolling window; sum of **final approvals** whose simulated timestamp ≥ current − 7×24h |
| `cart.purchases_in_period` + `scope: period`, `period_days: N` | integer; purchases on this card in the rolling window of N×24h before the current simulated timestamp: **final approvals + pending step-ups** (declines and expired step-ups never count; a redelivered live id counts once). This purchase is compared as count + 1: `<= 1`, `period_days: 1` is "one a day", so a second purchase fails. Operators `<=` / `<` only. Fail: `period_count_exceeded`; evidence "You allowed one order per day; one was already approved today at 12:10" (time of the latest approval, Europe/Zurich), message "Declined CHF 32.00: You allowed one order per day; one was already approved today at 12:10.", counterfactual "Would approve from tomorrow at 12:10."; a breach caused only by pending step-ups asks (`period_reserved_pending`, M5) |
| `merchant.merchant_category` | trusted catalogue category |
| `merchant.known_shop` | `"true"` if ≥1 approved purchase by this customer at this `merchant_id` on any of their cards (history + this run's finals); customer-level per rules.md Q7. `merchant.familiar_on_card` is accepted as an alias for the same check |
| `items[].item_category` | every cart line must satisfy `in` / `not_in` |
| `items[].contains_alcohol` | `"false"` ("no alcohol"): every cart line must be known not to be an alcoholic drink. `"true"` when the catalogue's text for the `item_id` or the shop's `item_name` / `item_details` names alcohol (allowlisted lexicon: wine, beer, spirits, Wein, Bier, vin, birra…); `"false"` for a line whose category cannot be a drink (household, electronics, …), or whose texts name no alcohol and no bare drinks word, or say "alcohol-free" / "soft drinks"; `unknown` when drinks are named without saying which, or alcohol and alcohol-free together. The catalogue naming alcohol wins over the shop's text. Fail: `item_mismatch`, "Wine and spirits is alcohol, which you excluded" |
| `items[].size_eu` | regex-extracted from `item_details`; `unknown` if absent |
| `items[].size_letter` | regex-extracted letter size (XS–XXXL, small/medium/large) from `item_details`; `unknown` if absent (C6, clothing) |
| `order.return_window_days` | regex-extracted from `item_details`; `unknown` if absent; `order_returnable == "false"` ⇒ 0 |
| `order.order_returnable` | the live string field |
| `cart.recurring` | `"true"` if any line is `subscriptions`/`membership` or text states recurring billing |
| `items[].unit_price_chf` | every cart line's `unit_price` converted to CHF (M1, M2); per-item limits |
| `items[].quantity` | every cart line's `quantity` |
| `cart.quantity` | total quantity of the requested item across all cart lines (all lines when no item is requested); "two tickets" is 1 line × 2 or 2 lines × 1 |
| `merchant.merchant_country` | trusted catalogue country, ISO 3166 alpha-2 (e.g. `"CH"`) |
| `merchant.merchant_city` | trusted catalogue city as the event spells it (e.g. `"Munich"`); `unknown` if the event has none. Fail: "SummitStay is in Lucerne, not Munich", counterfactual "Would approve at a shop in Munich" |
| `authorization.delivery_by` | the live `delivery_by` date, compared as a date; `unknown` if `null` |
| `authorization.weekday` | purchase time in Europe/Zurich, `"mon"`..`"sun"` |
| `authorization.local_hour` | purchase time in Europe/Zurich, 0–23; time-of-day rules |
| `unverifiable` | a stated restriction no field can check (e.g. "from the official ticket seller"); always `unknown`, so C11 applies. Evidence `Can't check "from the official ticket seller" from the data; you decide` (the rule's value, quoted once) |

C2 (`scope: period`) is not evaluated with the other customer rules: it needs the run's spending memory. C2 and remembered answers are added by the pipeline via `policy.add_ledger_results`, from the LedgerView's spent and reserved amounts (M4, M5), its purchase count (`period_count`, `period_reserved_count`: the same window and card as the spend) and `confirmed_keys`, so decide and explain both see them. The same step makes a known-shop check (`merchant.known_shop`, `merchant.familiar_on_card`, or the `requires_known_shop` flag, C9) `unknown` when the LedgerView knows no shop at all (no purchase history yet), with reason code `no_purchase_history`. `confirmed_keys` holds `rule|merchant|item` (read for `unverifiable` rules) and `rule|merchant|*` (read for a known-shop check: one yes covers the shop).

Extraction from `item_details` is allowlisted regex only, produces facts, never instructions.

Two per-line facts (`ItemFacts`, engine-side) are a **second source** after the trusted
fields, never in place of them. Both default to unknown, and unknown is never a pass:

| `ItemFacts` field | type | source | read after |
|---|---|---|---|
| `matches_requested` | `FactValue[bool]` | `regex` or `model` (tier 2) | the requested-item name match (C5, C10) |
| `delivery_date_text` | `FactValue[date]` | `model` (tier 2), from the shop's text | the live `delivery_by` (C12 `authorization.delivery_by`) |

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
  model rewrite on a later poll (`explanation_source: 'model'`). The worker posts the
  template, then, only while a provider is configured (D5 toggles it), rewrites it in the
  background and updates the stored decision's `message` and `explanation_source`. A
  failed or rejected rewrite leaves the template. A rewrite is rejected unless it contains
  verbatim every number (amounts, counts, times) and every shop and item name
  (case-insensitive) of the template message (`engine/tier3.py`).
- The engine reconciles `context.approved_spend_in_period_chf` from Viseca against its own
  ledger on every event, and each decision against the platform's event feed
  (`GET /v1/events?since=<cursor>`, advancing with the returned `next_cursor`) as well as
  against `context`; either mismatch is logged as an `info` evidence row. `ledger_mismatch`
  rows are **operator-only**: kept in the stored decision and the decision posted to
  Viseca, sent by C6 only with `?operator=1` (the operator console passes it); the
  customer's app never sees them (`api/models.py` `OPERATOR_ONLY_EVIDENCE`).
- A decline is posted to Viseca with one more evidence row, `{ kind: 'would_approve_if', rule:
  'would_approve_if', outcome: 'info', source: 'policy', detail: <counterfactual>,
  would_approve_if: Bound[] }`, so the agent learns what the customer would accept. It is not
  in `Decision.evidence`. A 400/422 on the body is retried without that row, then minimal.
- Every decision gets a signed `Receipt` (P4, docs/passport.md), issued by a background sweep
  off the decision's path (never delaying a post); a step-up's answer re-signs it. Its
  `evidence_hash` covers the stored evidence, operator-only rows included.
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
  customer answer through C8 sets `resolved_by: 'customer'` and re-renders the stored and
  served message as "Approved by you CHF {amount}: {clause}" / "Declined by you CHF
  {amount}: {clause}", the clause the customer was asked with kept verbatim (a tier-3
  rewrite is kept whole as the clause); the counterfactual is unchanged
  (`engine/ledger_base.py` `answered_message`).
- A step-up renders the **complete** purchase (all lines, delivery fee, currency, recurring
  flag, flagged text).

### 3.6 Revoke (C5)

- `DELETE /v1/mandates/{TM}` at Viseca; our mandate flips to `status: 'revoked'`, never
  deleted. Pending step-ups are shown as cancelled **only** after Viseca confirms their
  state. A revoked card can receive a new policy (new draft → new mandate).
- Viseca keeps one active mandate per team, so a policy confirmed on any card supersedes
  the others there. A DELETE answered 404 or 409 (already revoked or superseded) is done:
  C5 still answers `204` and logs the platform's status. Only another platform failure is
  a `503 upstream_unavailable` (our policy stays revoked; the customer may try again).
- Session freeze (`session.trust = 'frozen'`) is engine state, not a mandate change: after a
  burst the engine step-ups the next otherwise-clean purchase once (`session_watch`;
  `session_recovered` on approval), then relaxes; a no or a timeout keeps the watch on. The
  watch is per mandate: it carries into later live sessions under the same policy, and a new policy starts clean (rules.md W-rule 4). The step-up card may offer a shortcut to
  the existing RevokeSheet.

### 3.7 Soft signals (Laya)

- Off by default in the offline replay; on by default in live runs if the model loaded.
  D5 toggles at runtime.
- Timeout 500 ms (`ONEGUARD_SIGNAL_BUDGET_MS`); on timeout/error the keyword detector runs instead. Output only ever adds
  `evidence` rows with `source: 'model'` and may raise `approve → uncertain`. It can never
  lower `stopped` or override a policy check. `engine_version` records whether the model
  was on, so a replay with it off is comparable.
- `ONEGUARD_SOFT_SIGNALS`: `off` = no soft signal; `keywords` = the A1 pattern list;
  `laya` = triggered if keywords OR Laya fire (Laya can only add, never clear a keyword hit).
- Instruction readings (compiler): "under CHF X" = "Total under CHF X per order"; "two tickets" =
  `cart.quantity`; "the present I picked" = an `unverifiable` rule; "by Friday" = from the card's
  simulated date (docs/decisions.md, P4/P5 review).
- Tier-2 fact extraction and tier-3 explanation use the same provider interface as the
  compiler (OpenAI first, model-agnostic).

### 3.8 Errors

All errors: `{ error: { code: string, message: string, detail?: object } }`. Codes used:
`not_found`, `validation`, `draft_confirmed`, `lint_failed`, `not_pure_addition`,
`not_awaiting_answer`, `window_closed`, `upstream_unavailable`, `compiler_timeout`,
`internal`, `runs_disabled`, `run_active` (D3: an unfinished run is still followed),
and (NEW, §3.10, §1.3) `device_signature_required`, `device_not_enrolled`,
`signature_invalid`, `replay` (401), `last_device`, `device_state` (409), `forbidden`,
`not_controller` (403: P8–P10 signed by an enrolled device that is not the card's controller),
and (NEW) `operator_required` (401: `/api/dev/*` in production without the right
`X-OneGuard-Operator`, §1.2), `operator_unconfigured` (503: production with no
`ONEGUARD_OPERATOR_TOKEN` set), `rate_limited` (429: C1 over 10 drafts a minute for one
customer; `detail: { limit, window_s, retry_after_s }` and a `Retry-After` header).
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

### 3.10 Device-bound writes (NEW, docs/passport.md)

C2, C4, C5, C8 and P8–P10 change what an agent may do with a card, so they are accepted only
from a device enrolled on that card (C2: the draft's card; C8: the purchase's card). The
device holds a P-256 key pair it cannot export (WebCrypto `extractable: false`); OneGuard
keeps its public JWK. Each such request carries:

| Header | Value |
|---|---|
| `X-OneGuard-Device` | the `device_id` P7 returned for this card |
| `X-OneGuard-Ts` | Unix time in seconds when signed |
| `X-OneGuard-Nonce` | a fresh random string (≤ 128 chars) |
| `X-OneGuard-Signature` | base64 ECDSA-P256-SHA256 (raw `r‖s` or DER) over canonical JSON of `{ method, path, body, ts, nonce }`: `path` without the query, `body` the JSON body or `null` |

Refused with `401`, nothing applied: `device_signature_required` (a header missing),
`device_not_enrolled` (unknown device, another card's, pending or removed),
`signature_invalid` (the signature does not match this method, path, body, time and nonce),
`replay` (`|now − ts| > 120 s`, or the nonce was used before; nonces are kept 10 min). The
UI answers `device_not_enrolled` by opening the enrolment flow. C1 drafts and every GET stay
open. The card's first device enrols without approval and is its **controller**; later ones
wait `pending` until the controller approves them, then are **approved** devices. Every enrolled
device signs C2, C4, C5 and C8. Only the controller signs P8 (approve), P9 (remove) and P10
(transfer: another enrolled device becomes the controller, the signer stays approved); an
approved device gets `403 not_controller` and nothing changes. The controller cannot remove
itself while another device is enrolled (it transfers first), so an enrolled card always has
exactly one. The passport lists `controller_device_id` and each device's `role`, and is
re-issued on every change of who may sign or manage. Devices from before controllers
(`devices.controller_since` NULL) read the earliest enrolled device as the controller; a card
with none enrolled has none.

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
`period_count_exceeded` (a `cart.purchases_in_period` count is full: more purchases in the period than allowed),
`shop_terms_contradictory`, `rule_not_met` (a C12 rule with no specific code: per-item
price, quantity, country, weekday, delivery date), `unusual_activity` (two weak warning
signs), `session_watch` (the one ask after a burst, rules.md W-rule 4),
`no_purchase_history` (a known-shop check, C9, left unknown because the customer has no
purchase history yet; every other unknown keeps its own code, else unevaluable),
`no_active_policy` (rules.md §4 step 1: the card has no active policy of its own, because none
was confirmed, it was revoked, or the purchase came under another card's policy; message
"No spending policy is active on this card, so nothing your agent proposes can be paid
from it."; `card_or_authority_inactive` stays for a revoked authority or a blocked card).

Development only: `stub`, emitted only while `ONEGUARD_STUBS` stubs `decide`
(`backend/oneguard/engine/stubs.py`); never in a live run.

Any new code is added here before it is emitted. The UI maps codes to labels with a
neutral fallback for unknown codes; `frontend/src/lib/reasonCodes.ts` labels exactly this
list (no more, no less), and `frontend/tests/reasonCodes.test.ts` holds it and the mock
fixtures to it.

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
3. `Decision.counterfactual` — one line under the message in DecisionDetail and on the Approvals pending card; when the message ends with the same suggestion, both drop that trailing copy so it is said once (lists keep the full message).
4. `Mandate.usage` — `lib/spend.ts` prefers it when present; keep client math as mock fallback; ensure human-approved step-ups count as spend.
5. `Evidence.outcome: 'info'` — neutral styling; unknown values fall back to neutral.
6. Optional: `Decision.session` banner on DecisionDetail when trust ≠ normal; a "Revoke policy" shortcut on the Approvals card.
7. Fixtures: add the new fields to `build_decisions_fixture.py` / `build_policy_fixture.py` so mock mode matches.
8. `Decision.explanation_source` and `Decision.resolved_by` in `types.ts`; `mergeDecisions.sameDecision` also compares `explanation_source` and `counterfactual` so a tier-3 rewrite re-renders.
9. Policy screen renders DryRunResult.examples and dry_run.agent_history as one line; agent_history is customer-level and labelled "across your cards", the dry run stays card-scoped and says so ("on this card"). `Decision.explanation_source` tag strings: `template` → "Explained by OneGuard", `model` → "Wording refined by AI · decision made by your rules"
10. PolicyDraft.compiler == 'fallback' shown as a banner; Decision.explanation_source shown as a subtle tag
11. Optional: `Mandate.usage.confirmations` as a "Things you've confirmed" list on the policy screen (names rendered as plain text)
12. Optional: `Decision.confirmable` - on a step-up, "Approve, and treat <shop> as <phrase> from now on"; absent or null means the ordinary approve button
13. Policy review: a draft with no checks disables "Confirm policy" (C2 would refuse it, §3.2) and shows its `open_questions`, falling back to "I couldn't read a spending limit or item type - try 'groceries, max CHF 120 per order'" when there are none
14. `Decision.run_id` / `run_started_at` - Activity and Home list each card's newest run (latest `run_started_at`); older runs sit collapsed under "Earlier runs (n)", each headed by its start time. A decision without `run_id` is always listed. Approvals is unchanged (pending only)

15. Approvals pending card: `uncertainty.note` shows only when it says something the message does not (`lib/decisionMessage.ts` `noteAddsToMessage`); the note is usually the uncertain evidence row's detail, which the card lists anyway.
16. Operator strip (`?demo=1`, operator only): reads D5 GET on each poll and labels the toggle with what the server reports (on, off, live only, replay only), never an assumed "on"; D1's 404 reads as "no replay yet", not as the backend being unreachable.
17. `Decision.policy_applied` - DecisionDetail's "Policy applied" shows these checks (the rules that decided), with a note when they are the platform mandate's rules or differ from the card's current policy (another mandate id and other checks: a D3 move keeps the checks under a new id); the card link (manage / revoke) stays. Absent: the card's current policy, as before.
18. Home follows each poll: `OverviewHero` counts the same decisions as Activity's filter chips (each card's newest run, `lib/runs.ts` `countDecisions`, no simulated-day window), and each "Active policies" row shows its `Mandate.usage` (re-read by C3 after every decisions poll): "CHF x of CHF y" with a bar and "This week · n purchases" for a period limit, "CHF x spent" and "This run · n purchases" without one, plus what is waiting.
19. Operator console at `/ops` (desktop, operator only, never linked from the phone UI): a separate page of the same build (`src/ops/`), reading `/healthz`, D2, D3, D4, D5, D7, D8, D9, C6 (with `?operator=1`, §3.4) and C3's card, plus the passport endpoints (§1.3): the card's passport line (version, checks, devices, QR) is checked with P5, and each decision with a `receipt_id` has its receipt (P4) checked with P5. It follows D7: whatever run started last, from any source, is the one its run header, stream and embedded phone show while it is in progress (starting, running, or with step-ups waiting); once it is not, the run header says "No run in progress." and shows that finished run, labelled finished, only while its scenario is selected; a line under the buttons names it ("Last run: Replay from record · SCEN0101 · Omar Chen (CU1217, card CA1331) · running"), apart from the "Sign in as <name> (<customer id>, card <card id>)" line, which follows the selected scenario's customer (D9). Its Replay buttons (D2) replay a served scenario from record, saying so under them and in the run header ("Replay from record", with the recorded run's start); for a scenario D9 marks `replay_source: null` they are disabled with "Not run yet: no stored events to replay." Its "Judging run (live)" (D3) is disabled, with the reason on the button and under it, for a scenario D8 does not mark `served` ("Replay only: not served by the sandbox.", said first: no worker state changes it), and while `/healthz` shows the worker off (`worker.configured` false) or not polling (`worker.state` other than `polling`), or has not answered, or D8 or the runs on record have not been read. A served scenario with a finished live run on record (the card's `live-<id>` runs in C6, each read with D4 for its `scenario_id` and `state: done`) keeps the button enabled with a warning, "Already on record (run <start>)", repeated in the typed-id confirmation. It never answers a step-up and never enrols, approves or removes a device; the customer does that on the phone. It wears Viseca's colours and type (Roboto via Google Fonts, system fallback); the embedded phone keeps its own. The run header names a replay's policy from D7's `policy_source`: "the card's active policy", "policy revoked: every purchase declines" or "policy compiled from the scenario"; for a live run whose `platform_mandate.reregistered` is true it adds "platform mandate re-registered" (its tooltip names both platform mandates and the status the old one had). Its left column has three tabs: Overview (scenario, instruction, buttons, run header, the last 5 decisions), Decision log (the run's stream, filtered by outcome and shop, rows expandable, Export JSON of the visible rows) and Health (the `/healthz` body as a table, with a copy button).
20. Phone UI deep links, both read once at load: `?customer=<customer_id>` signs in as that customer and skips the picker (session only, nothing stored; an unknown id shows the picker); `?embed=1` draws the phone UI without `DeviceFrame`'s bezel, for the console's embedded phone (an iframe of `/?customer=<id>&embed=1`, 390×844).
21. Sign-in footer: "Powered by OneGuard" (small), the same line the console carries.
22. Passport (§1.3, §3.10, docs/passport.md): `lib/deviceKey.ts` (a non-extractable P-256 key in IndexedDB, `signedFetch` for C2, C4, C5, C8, P8, P9, P10; a `device_not_enrolled` answer opens enrolment); NewPolicy confirm enrols this device first (the card's first device silently, otherwise "This device isn't approved for this card yet"), and a card's first passport (`Mandate.passport.version == 1`, none before) gets step 3 "Your passport is issued" with the first-passport animation; CardDetail gains a Passport section (QR, version, devices with Approve / Remove, Verify; when no device controls the card, "Make this device the controller" enrols this browser through P7 as the card's first device, trust on first use, and re-reads the re-issued passport, while a card another device controls keeps "Add this device", the pending path; `lib/passportDevices.ts`). The section names the controller ("Controller · this device" or its label; badges Controller / Approved); only the controller sees Approve / Remove and, on each other enrolled device, "Make this the controller" (P10, asked once more before it is sent); an approved device reads that it can change the policy and answer requests while the controller manages devices, and a pending one "Waiting for approval from <controller label>" (the enrolment sheet too). Home shows "Devices waiting for your approval" on the controller only (polled every 5 s); DecisionDetail shows "What your agent was told" (`would_approve_if`, in words from `counterfactual`) and "Receipt · Verify"; a `/verify` page for the QR link. Mock fixtures gain a passport, two devices and a receipt.

23. Operator token (§1.2): every `/api/dev/*` call from the console and the `?demo=1` strip goes
    through `api/operatorFetch.ts`, which sends `X-OneGuard-Operator` from `sessionStorage`
    (`lib/operatorToken.ts`). A `401 operator_required` asks for the token once, however many
    calls were refused together, keeps it for the tab's session and retries; a wrong token asks
    again ("That token was not accepted"). The console asks in its own dialog, Viseca-styled like
    the judging-run dialog; the strip, which has no dialog, uses the browser's prompt.
24. NewPolicy describe: the instruction textarea stops at 1,000 characters and counts them
    (C1 refuses more with 422); a C1 `429 rate_limited` shows the server's sentence on the step-1
    error screen ("Too many drafts") instead of "The AI took too long".

No customer endpoint changes. No screen removals. Tighten UI stays dormant.

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

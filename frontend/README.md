# Wallet control UI (frontend)

A mobile-first React PWA: the customer's view of a wallet control layer for AI shopping agents.

A customer states what an AI shopping agent may spend on their card, reviews every decision the
engine made with its reasons and evidence, answers step-ups within a 120-second window, and
tightens or revokes the policy.

**The UI never makes or changes a decision.** It renders what `/api` tells it. All data is
synthetic.

This README is self-contained: everything needed to run, navigate, and safely change this app is
below. The canonical contract is `../docs/api-contract.md`, which now also holds the backend's
side (what it must return and guarantee); `API-CONTRACT.md` in this folder only points there. Its
§6 is the only list of changes this UI takes; where this README differs, that one wins.

**Contents**

1. [Run it](#1-run-it) · 2. [What the app does, screen by screen](#2-what-the-app-does-screen-by-screen) ·
3. [Project structure](#3-project-structure) · 4. [Architecture decisions](#4-architecture-decisions-read-before-changing-anything) ·
5. [The API contract](#5-the-api-contract) · 6. [Hard rules for UI code](#6-hard-rules-for-ui-code) ·
7. [Design system](#7-design-system) · 8. [Fixtures](#8-fixtures-and-mock-mode) ·
9. [Build status](#9-build-status) · 10. [Known gaps](#10-known-gaps) · 11. [How to add a feature](#11-how-to-add-a-feature)

---

## 1. Run it

```bash
npm install          # only when package.json changed
npm run dev          # http://localhost:5173
npm run build        # tsc -b && vite build  → dist/
npm run lint         # eslint
npm test             # node --test on tests/*.test.ts (Node's built-in runner, no dependency)
```

There is no router. `npm run build` (which type-checks first), `npm run lint` and `npm test` are
the whole gate — run all three before you hand work over. `npm test` covers pure logic in
`src/lib/` only; there is no component test setup.

### Two modes, one set of call sites

| `.env` | What happens |
| --- | --- |
| `VITE_USE_MOCKS=true` | Each `src/api/*.ts` function resolves from a local JSON fixture. No network. Works with the backend switched off. |
| `VITE_USE_MOCKS=false` | The same functions `fetch` from `VITE_API_BASE_URL`. |

`.env` (gitignored — copy `.env.example`):

```
VITE_API_BASE_URL=/api
VITE_USE_MOCKS=true
```

`VITE_API_BASE_URL` is **relative on purpose**. The same build runs behind the Vite dev server,
behind the backend's own static mount, and behind any single-origin deployment — no rebuild, no
CORS.

### Against the real backend

Set `VITE_USE_MOCKS=false`, then either:

```bash
# A. dev server + proxy (keeps hot reload) — vite.config.ts forwards /api → :8000
#    terminal 1:
cd ../backend && source .venv/bin/activate && uvicorn oneguard.api.app:app --reload --port 8000
#    terminal 2:
npm run dev

# B. one origin: the backend serves the built app AND the API
npm run build
cd ../backend && source .venv/bin/activate && uvicorn oneguard.api.app:app --port 8000
# → http://localhost:8000   (also reachable from a phone on the same wifi)
```

Option B is the one to use for a demo on a real phone. If `/api` calls come back as HTML, the
backend mounted its static files before its API routes — that mount has to come last.

**Never put an API key in a `VITE_` variable.** Anything with that prefix is compiled into the
public bundle. The payment-platform key is server-side only; the browser knows exactly one backend,
our own `/api`.

---

## 2. What the app does, screen by screen

| Screen | What the customer does |
| --- | --- |
| **Sign in** | Picks a customer. Up to four live customers (a scenario behind them) are shown, the one backing the most recent scenario first; everyone else, live or not, is in the "select other" sheet (`src/lib/signInCustomers.ts`). Session-only — a reload signs you out. |
| **Home** | Hero summary of recent proposals, a "Needs your review" card while a step-up is pending, the three most recent decisions, and a read-only list of cards that have an active policy. Tapping a count or "See all" opens Activity pre-filtered. |
| **Activity** | The decision feed. Underline filter tabs, rows grouped by date. Every row opens the detail screen — except a still-pending one, which routes to Approvals, because it is actionable rather than just viewable. |
| **Approvals** | The step-up inbox. One pending purchase at a time with a live countdown, full merchant and basket detail, the evidence, a preview of what approving would do to the spend meter, and Approve / Reject. Plus an "Also waiting" strip and an expired section. |
| **Decision detail** | Why this decision happened: the facts banner, evidence rows marked pass / fail / uncertain, a box showing the shop's own text when injection was flagged, which policy applied, and other decisions on the same card. |
| **Card detail** | One card: the customer's instruction, what their rules do in plain language, the spend meter, revoke, and that card's activity. |
| **Accounts** | Accounts → cards. A card with a policy opens card detail; one without offers "+ Add policy". |
| **New policy** | Describe it in your own words (AI reads it) or fill in a form (no AI). Then review every check with its uncertainty and open questions, choose what happens when we are unsure, and confirm. |

Two deep links, read once at load (`src/lib/deepLink.ts`): `?customer=<customer_id>` signs in as
that customer and skips the picker (session only), and `?embed=1` drops `DeviceFrame`'s bezel.

**Operator console (`/ops`)** is a separate desktop page of the same build, never linked from the
phone UI (`src/ops/`, loaded lazily by `src/Root.tsx`): for the projector. It follows the current
run (D7) whoever started it, lists its decisions (C6) newest delivered first with expandable
evidence and receipts, starts replays (D2) and judging runs (D3, typed confirmation), shows
`/healthz` as chips and embeds this app as the run's customer (`/?customer=<id>&embed=1`). It never
answers a step-up or touches a device. Contract: `../docs/api-contract.md` §6 item 18.

---

## 3. Project structure

```
src/
  main.tsx                  entry
  App.tsx                   the whole navigation shell (no router — see §4)
  config.ts                 HUMAN_WINDOW_SECONDS (120), DECISIONS_POLL_SECONDS (5)

  api/                      one module per capability; each holds the mock/real branch
    types.ts                  every shared type — the contract in TypeScript
    customers.ts              C12  getCustomers
    decisions.ts              C6   getDecisions
    approvals.ts              C8   resolveApproval
    accounts.ts               C10  getAccounts
    policy.ts                 C1–C5 compilePolicy / confirmPolicy / getPolicy /
                                    tightenPolicy / revokePolicy

  state/                    React context only — no Redux / Zustand / React Query
    CustomerContext/Provider  who is signed in (session-only)
    PolicyContext/Provider    mandates keyed by card_id, loaded from C3 (see §4)
    DecisionsContext/Provider polls C6, expires lapsed step-ups locally, resolves step-ups
    mergeDecisions.ts         the ONLY place incoming decisions become state (see §4)

  screens/
    SignIn/                   customer picker + "select other" bottom sheet
    Home/                     hero, "Needs your review", "Latest by your control", active policies
    Activity/                 decision feed, underline filter tabs, date-grouped rows
    Approvals/                step-up inbox: one pending card at a time + "Also waiting" strip
    DecisionDetail/           banner, evidence, injection box, policy applied, related decisions
    CardDetail/               one card: instruction, what the rules do, meter, revoke, activity
    Accounts/                 accounts → cards → card detail or "+ Add policy"
    NewPolicy/                Flow · Shell · Describe · Form · Reading · Timeout · Check

  ops/                      the operator console at /ops (desktop only, see §2)

  components/               TabBar, DeviceFrame, StatusBar, BottomSheet, CardPicker,
                            DecisionMark, CountdownBar, LeashMeter, OverviewHero,
                            AccountMenu, RevokeSheet, icons/ (lucide re-exports)
  lib/                      money.ts    CHF formatting
                            datetime.ts UTC display of simulated time
                            spend.ts    client-side spend vs. the policy's own limits
                            initials.ts avatar initials
  styles/tokens.css         design tokens; src/index.css maps them into Tailwind's @theme
  mocks/fixtures/*.json     customers, decisions, accounts, policy-drafts

scripts/build_*_fixture.py  regenerate those fixtures from the synthetic data pack (§8)
public/                     manifest.webmanifest, icon.svg, sw.js (a deliberate no-op)
```

### Stack

TypeScript · Vite 8 · React 19 · Tailwind v4 (`@tailwindcss/vite`) · `lucide-react` for icons.

**Ask before adding a dependency.** Deliberately absent: router, state library, data-fetching
library, test runner, mock-server library, PWA plugin. Each has been considered and not needed yet.

---

## 4. Architecture decisions (read before changing anything)

**Mock mode is a per-resource swappable function, not a mock server.** Each `src/api/*.ts` function
checks `VITE_USE_MOCKS` and either imports a fixture or calls `fetch`. Same signature, same call
sites either way — removing mock mode later is deleting an `if`, not rewriting screens. Follow this
pattern for any new capability. (A service-worker mock library was considered and rejected; no
service worker is involved in mocking.)

**One decisions store, not two.** Activity and Approvals are both views over `DecisionsProvider`.
A pending step-up is *not* a separate type or endpoint — it is a `Decision` with
`status: 'pending_human'` and `deadline_at` set. Resolving or expiring one updates the single list,
so both screens agree instantly, and the tab badge stays correct for free.

**Every incoming decision goes through `state/mergeDecisions.ts` and nothing else.** That function
knows nothing about transport: polling calls it with a list today, a server-sent-events stream will
call it with one decision later, a reconnect with a list again. Keeping that seam is the only
reason swapping transport touches no screen. Two pieces of local state deliberately beat the
server there:

- a **running countdown** — `deadline_at` is real-clock, so re-taking the server's value on every
  poll makes the bar stutter; the first value seen wins;
- a **locally expired step-up** — the server may still say pending; letting it win would flip the
  row back to "waiting for you" and restart the clock. An unanswered step-up ends *paused*, not
  decided.

A failed *refresh* keeps what is on screen — those decisions are still true, just not fresh. Only a
failed *first* read shows the error state.

**Polling, every 5 seconds, while signed in.** The agent proposes purchases while the customer has
the app open, so a screen that only loaded on sign-in would quietly go stale.

**No router.** Screens switch through local state in `App.tsx` (`tab`, `flowCardId`, `cardDetailId`,
`activityFilter`). Card detail is App-level state rather than owned by Accounts, because a
decision's "Policy applied" link reaches it from another tab. Adding a router is fine — it just has
not been needed.

**No state library, and no persistence.** React context has been enough. Who is signed in is
session-only; sign-out is client-side and stores nothing. A reload signs the customer out.

**Policies come from C3, never from the session.** `PolicyProvider.refreshPolicies` reads every
card's policy (`getPolicy`) and replaces what is held: on sign-in, after every decisions poll, after
a step-up answer (C8), and after confirm (C2) or revoke (C5). So a reload and a new sign-in show the
same policies, and the spend meter reads the ledger's current `usage`. Until the first round is back
no card says "No policy yet"; "+ Add policy" appears only for a card C3 answered `null` for (a
revoked card still has its revoked mandate). In mock mode `getPolicy` reads what the mock C2/C4/C5
wrote.

**A revoked policy is never deleted, only flipped to `status: 'revoked'`.** Otherwise "never had a
policy" and "had one, revoked it" become indistinguishable when reviewing older activity, and a
past decision could not explain which policy produced it.

**Three decision categories, not four:** `approved | stopped | uncertain`. Expiry and a human's
answer are sub-statuses of *uncertain* (`uncertain_outcome`), never their own category — the
history has to keep showing which purchases needed a person.

**`DeviceFrame`** wraps the root shell: at ≥640px the unchanged 390px UI is drawn as a literal
phone (bezel, notch, capped height); below that it is a no-op, identical to a plain mobile layout.
Three consequences to respect:

- `fixed` overlays resolve against the **screen** div, which carries `sm:transform-gpu`. Move that
  class and overlays escape the frame or paint across the bezel.
- The app's only `createPortal` targets `OVERLAY_HOST_ID` inside that div — never `document.body`,
  which would float the menu over the whole browser window.
- `BottomSheet` caps at `max-h-[60%]` of that box, never a `vh` unit of the browser window: inside
  the frame those are different boxes, and `vh` lets a long sheet cover almost the whole phone.

---

## 5. The API contract

Canonical: [`../docs/api-contract.md`](../docs/api-contract.md); this section mirrors it for the UI.
Base URL `/api`. These nine capabilities are the entire surface between this app and the backend.
The types below are mirrored in `src/api/types.ts`, which is the authority for the frontend.

> The response envelope is **not uniform** — some endpoints wrap their payload in a named key,
> some return the object bare, some return `204`. The client depends on the exact shape per row.

### Endpoints

| # | Method | Path | Request body | Success response |
| --- | --- | --- | --- | --- |
| C12 | `GET` | `/api/customers` | — | `{ "customers": Customer[] }` |
| C6 | `GET` | `/api/customers/{customer_id}/decisions` | — | `{ "decisions": Decision[] }` |
| C10 | `GET` | `/api/customers/{customer_id}/accounts` | — | `{ "accounts": Account[] }` |
| C1 | `POST` | `/api/cards/{card_id}/policy-drafts` | `{ "instruction": string }` **or** `{ "form": FormInput }` | `PolicyDraft` (bare) |
| C2 | `POST` | `/api/policy-drafts/{draft_id}/confirm` | `{ "checks": RuleCheck[], "uncertainty_policy": "ask" \| "decline", "open_questions": string[] }` | `Mandate` (bare) |
| C3 | `GET` | `/api/cards/{card_id}/policy` | — | `{ "mandate": Mandate \| null }` |
| C4 | `POST` | `/api/cards/{card_id}/policy/tighten` | `{ "add_checks": RuleCheck[], "uncertainty_policy"?: "decline" }` | `Mandate` (bare) |
| C5 | `POST` | `/api/cards/{card_id}/policy/revoke` | — | `204` |
| C8 | `POST` | `/api/authorizations/{authorization_id}/resolve` | `{ "decision": "approve" \| "decline" }` | `204` |

Suggested error codes — the client treats every non-2xx alike, so these are for the backend's
hygiene rather than something the UI branches on: `404` unknown customer / card / draft /
authorization; `422` a C1 body that is neither `instruction` nor `form`; `409` a tighten that is
not a pure addition, or a resolve for a step-up that is not awaiting an answer, **including one
whose window already closed**.

### Notes that save time

- **C6 also serves the step-up inbox** — filter to `status: 'pending_human'`. There is no separate
  endpoint and no `Approval` type.
- **Running spend comes from C3.** The meter reads `Mandate.usage` (the engine ledger). `lib/spend.ts`
  computes it client-side from C6 plus the policy's own checks only when `usage` is absent (mock
  mode's form and fallback drafts).
- **C2 deliberately carries a body**, unlike the payment platform's own bodyless mandate confirm.
  The customer can change `uncertainty_policy` on the review screen before confirming, so the
  frontend sends back what they actually approved. Our `/api` is a wrapper, not a 1:1 proxy.
- **C4 is dormant** — implemented in `src/api/policy.ts` and called from nowhere. It has no UI
  because the tighten screen was built and then pulled.
- **A lapsed step-up should be closed server-side too**, so the expiry survives a reload rather
  than living only in the open tab.
- **Operator endpoints for starting or restarting a scenario run are outside this contract.** The
  UI never calls them: a run is a demo-operator mechanism the customer never sees.
- **A live event stream is proposed, not built.** It would complement C6 polling and merge through
  the same `mergeDecisions` function, touching no screen.

### Types

```ts
Customer  { customer_id, name, home_region,
            card_id: string|null, scenario_id: string|null,
            live: boolean }                      // false ⇒ card_id and scenario_id are null

Card      { card_id, card_type, card_purpose, status }

Account   { account_id, customer_id, account_type, account_purpose, status,
            per_transaction_limit_chf, monthly_limit_chf,   // BANK limits — never on the meter
            cards: Card[] }                                 // [] if none, never null

RuleCheck { id, text,                        // plain language — rendered verbatim, see below
            source: 'exact' | 'inferred',    // 'exact' = the customer's own wording
            uncertainty: string | null }

FormInput { per_order_limit_chf: number|null, period_limit_chf: number|null,
            period_days: 7|14|30|null, categories: string[],
            sellers_used_before_only: boolean,
            uncertainty_policy: 'ask' | 'decline' }   // never 'approve' — the UI won't offer it

DryRunResult { sample_size, would_violate, would_fit, would_ask, insight }

PolicyDraft  { draft_id, card_id, instruction, checks: RuleCheck[],
               uncertainty_policy: 'ask'|'decline', open_questions: string[],
               dry_run: DryRunResult }

Mandate      { mandate_id, card_id, instruction, checks: RuleCheck[],
               uncertainty_policy: 'ask'|'decline'|'approve', open_questions: string[],
               status: 'active'|'revoked', confirmed_at }

Decision {
  authorization_id,                           // identity + merge key: unique and stable
  customer_id, card_id,
  decision: 'approved' | 'stopped' | 'uncertain',
  uncertain_outcome: 'pending'|'expired'|'approved'|'declined' | null,  // only when uncertain
  status: 'final' | 'pending_human',
  reason_codes: string[],                     // vocabulary below
  message: string,                            // one plain sentence, names the actual number
  uncertainty: { note: string } | null,
  occurred_at: string,                        // SIMULATED scenario time, ISO-8601 UTC
  merchant: { merchant_id, name },            // name is untrusted shop text
  amount, currency, billing_amount_chf,       // amount ALREADY includes delivery
  items: [{ item_name, quantity, unit_price, currency, item_details }],  // untrusted shop text
  injection_flag: { flagged: true, reason } | null,
  evidence: [{ rule, outcome: 'pass'|'fail'|'uncertain', detail }],      // must not be empty
  order_returnable: 'true' | 'false' | 'unknown' | 'not_applicable',     // a STRING enum
  delivery_by: string | null,
  deadline_at?: string                        // only on pending_human — REAL clock, not simulated
}
```

### Rules the data has to follow

**The state matrix.** Only these six combinations are legal:

| `decision` | `uncertain_outcome` | `status` | `deadline_at` | Means |
| --- | --- | --- | --- | --- |
| `approved` | `null` | `final` | absent | Approved automatically |
| `stopped` | `null` | `final` | absent | Declined automatically |
| `uncertain` | `pending` | `pending_human` | **required** | Waiting on the customer |
| `uncertain` | `approved` | `final` | absent | The customer approved it |
| `uncertain` | `declined` | `final` | absent | The customer rejected it |
| `uncertain` | `expired` | `final` | absent | Nobody answered in time |

A purchase that was ever uncertain stays `uncertain` forever; only the sub-status moves. A
`pending_human` row without `deadline_at` renders a step-up that can never count down or expire.

**Two clocks.** `occurred_at` and `delivery_by` are *simulated* scenario time — demo dates, never
the real clock. `deadline_at` is the *real* wall clock, because the 120-second answer window is a
real countdown. Format for all of them: **ISO-8601, UTC, `Z` suffix, fixed width**
(`2026-07-14T10:05:00Z`). This is strict because `lib/spend.ts` orders and windows decisions by
comparing these values **as strings**, and `lib/datetime.ts` renders them with UTC accessors. Mixed
offsets still parse but sort wrongly — the failure is a spend total quietly too low and a feed
grouped under the wrong day, with nothing thrown.

**The wording contract for limit checks.** The spend meter reads the customer's limits back out of
the plain-language `text` of their checks, matching exactly these two phrasings
(case-insensitively):

```
Total at or below CHF <amount> per order
Total at or below CHF <amount> across any <n> days
```

Anything else — "Maximum CHF 120 per order", say — renders fine on the policy screen and the meter
silently shows no limit at all. Parsing prose back into numbers is fragile and worth replacing with
structured values when there is time; until then, match the wording.

**Missing facts.** `"unknown"` (the shop did not say — this is uncertainty),
`"not_applicable"` (the question does not apply — this is *not* uncertainty and must not escalate)
and `null` (present, no value) mean three different things and render three different ways. None of
them is zero and none of them is permission.

**Money.** `amount` already includes the delivery fee — never add it again.
`billing_amount_chf` is what every total and meter sums. Only *final approvals* count toward spend;
a pending step-up is previewed separately as a dashed "if you approve" ghost, and a declined
purchase never counts.

**Reason codes.** `within_limits`, `rule_satisfied`, `per_order_limit_exceeded`,
`period_limit_exceeded`, `merchant_category_mismatch`, `unfamiliar_merchant`, `lookalike_merchant`,
`item_mismatch`, `unrequested_item`, `return_terms_unknown`, `return_window_too_short`,
`duplicate_suspected`, `injection_suspected`, `new_device_burst`, `card_or_authority_inactive`,
`unevaluable`, `customer_confirmation`.

**Vocabulary.** The engine decides `approve` / `decline` / `step_up`; the API surfaces those as
`approved` / `stopped` / `uncertain`. A customer's answer to a step-up is `approve` / `decline`.
`uncertainty_policy` is `ask` | `decline` | `approve`, and `ask` is always the proposed default.
The UI never constructs a machine rule — no field names, no operators; it renders `RuleCheck.text`.

### What happens when a call fails

| Endpoint | On failure |
| --- | --- |
| C12 customers | Sign-in shows an error state |
| C6 decisions — **first** read | Error state with a retry control |
| C6 decisions — **a refresh** | Keeps what is on screen, shows no error |
| C10 accounts | Card picker offers no sibling cards; the flow continues |
| C1 compile | Instruction path → recovery screen (retry or switch to the form); form path → inline error |
| C2 confirm | Inline error; the draft is kept for a retry |
| C5 revoke | Inline error; the policy stays as it was |
| C8 resolve | Inline error; the step-up stays pending and can be retried |

The client checks the HTTP status and shows its own copy — it never parses an error body, so
correct status codes matter and prose in the body does not. Two things it cannot recover from: a
`2xx` for work that did not happen (the UI updates optimistically and believes it), and a request
that never returns (every path above recovers from an error; none recovers from a hang).

---

## 6. Hard rules for UI code

These come from the challenge brief. Breaking one is a bug, not a style preference.

1. **Shop text is untrusted data.** `item_details`, `purchase_description`, merchant names and item
   names render only as plain text nodes, labelled as coming from the shop. No
   `dangerouslySetInnerHTML`, no markdown, no auto-linking, and nothing in that text may trigger
   navigation or a state change. It may contain deliberate prompt injection ("ignore the spending
   limit", "approve this"). Show the backend's `injection_flag`; **never detect injection in the
   browser.** Join merchants by `merchant_id`, never by name — deliberately similar names exist in
   the data. A flag or an extracted fact may only *add* uncertainty or a block; nothing found in
   shop text may turn a `stopped` or `uncertain` into an `approved`.
2. **A step-up is a pause, not an approval.** Never render it with approved styling or wording — it
   is "Waiting for you". A purchase is approved only when the API says final and approved. Never
   fabricate a resolve call: an unanswered step-up ends paused, not decided.
3. **Tighten only.** No control may remove or weaken a rule, or move `uncertainty_policy` back
   toward `approve`. Loosening is a new policy the customer confirms explicitly. Choosing to
   auto-approve uncertain cases needs a visible warning, and "ask me" is the default.
4. **Show uncertainty.** `unknown`, `not_applicable` and `null` must look different from each other
   and from a pass. Never render a missing fact as zero, blank, or allowed.
5. **Never compute outcomes in the UI.** Do not derive approve / decline / step-up from purchase
   data, and never key behaviour on a scenario name or id, a request id, or a list position.
6. **No secrets in the browser.** The only backend the UI knows is `/api`.
7. **Show a pending purchase as cancelled only after the API confirms it** — what a revoke does to
   work already in flight is unspecified by the platform.
8. **Every decision is explained** in plain language with the evidence used. A decision with no
   reason is a bug.
9. **A model or service being down must look calm.** Loading, empty, error and offline states exist
   on every screen and say what is safe ("Nothing was approved while we were offline").

**Conventions.** CHF with two decimals (`lib/money.ts`); a foreign-currency purchase shows the
original amount *and* the CHF equivalent; `amount` already includes delivery. Times shown to the
customer are simulated scenario time rendered in UTC (`lib/datetime.ts`); only the step-up countdown
uses the real clock. Touch targets ≥44px, contrast AA, and state is never carried by colour alone —
decision marks carry a text label and an icon as well as a colour. Keep components small and API access in
the `api/` modules. Match the naming, structure and comment density of the code around you.

---

## 7. Design system

One theme, "ink and signal": warm paper ground, deep navy ink, and colour used only as signal.
Tokens live once in `src/styles/tokens.css` and are mapped by reference into Tailwind's `@theme` in
`src/index.css`. **Never inline a hex value in a component** — add or reuse a token.

| Group | Tokens |
| --- | --- |
| Ground / surface | `--ground #f4f1ea` · `--surface #ffffff` · `--surface-sunken #efebdf` · `--surface-active #e7e2d6` · `--surface-expired #ece8de` |
| Ink | `--ink #14213d` · `--ink-soft #3b4458` · `--ink-muted #5b6478` · `--ink-tab #5f6779` |
| On ink | `--on-ink #f4f1ea` · `--on-ink-soft #d5dbea` · `--on-ink-muted #aeb7cc` · `--on-ink-rule` |
| Lines | `--hairline #e2ddd1` · `--border-quiet #dad4c5` · `--border-dashed #c9c2b1` |
| Approved | `--approved #1b7a4b` · `--approved-tint #e3f3ea` · `--approved-on-ink #5fd39a` |
| Stopped | `--stopped #b42318` · `--stopped-tint #fbe9e7` · `--stopped-border` · `--stopped-on-ink` |
| Uncertain | `--asked #8a5300` · `--asked-ink` · `--asked-tint #fdf0d5` · `--asked-border` · `--asked-track` · `--asked-hatch` · `--asked-dot` · `--asked-on-ink` |
| Meter | `--leash-fill #14213d` · `--leash-limit #e0561b` (+ `-on-ink` variants) |
| Accent | `--cord-accent #7c3aed` · `--cord-accent-on-ink #b794f6` · `--tab-active #e9e1f6` |
| Countdown | `--countdown-track #eae1f6` · `--countdown-fill` (aliases the accent) |
| Other | `--alert-badge` · `--destructive` · `--destructive-border` · `--scrim` · `--card-icon-gray` · `--device-bezel` · `--device-backdrop` |

Spacing `--space-1…9` (4, 6, 8, 10, 12, 14, 16, 20, 24px). Radii `--corner-stamp 3` ·
`--corner-meter 7` · `--corner-quote 10` · `--corner-tile 12` · `--corner-button 14` ·
`--corner-row 16` · `--corner-card 20` · `--corner-hero 24` · `--corner-sheet 28` ·
`--corner-pill 999`. Type: `--typeface-display` (Bricolage Grotesque) and `--typeface-body`
(Instrument Sans), both falling back to Avenir Next then system UI.

Two token groups are deliberately kept apart even though they look similar: the countdown bar has
its own `--countdown-*` tokens rather than borrowing the "uncertain" family, so retuning the
ticking clock never implies a change to the uncertain semantic; and `--tab-active` is split from
`--surface-active` so the active tab pill can be tinted without dragging other chips with it.

**Icons come from `lucide-react`,** re-exported through `src/components/icons/`. Do not hand-author
an SVG icon — check the library first.

---

## 8. Fixtures and mock mode

`src/mocks/fixtures/*.json` are **UI fixtures, not engine output.** Every merchant name, amount,
item text and timestamp is joined by ID from the synthetic data pack; the hand-curated part is only
*which* real record illustrates *which* UI state, because the data pack deliberately ships no
answer key. The policy dry-run counts are computed from real history rather than invented.

They exist to exercise UI states — approved, stopped, uncertain, a pending step-up, an expired one,
missing facts, a flagged injection, spend near a limit — not to replicate a decision engine. Every
fixture is marked `mock: true`.

Regenerate them (the scripts read the CSVs from the data pack at `../data/`):

```bash
python3 scripts/build_decisions_fixture.py
python3 scripts/build_accounts_fixture.py
python3 scripts/build_policy_fixture.py
```

**Never copy these fixtures into the backend.** Fabricated engine output presented as real
decisions is exactly what this project's rules forbid. And only mock endpoints that exist in the
contract — if one is missing, agree it first rather than inventing it.

Two mock-mode demo affordances, both matched on the customer's own text and never on a scenario id:

- typing **"force timeout"** into the instruction box reaches the compile-timeout recovery screen
  (mock mode has no model call that could actually time out);
- `DEMO_SECONDS_REMAINING` in `src/api/decisions.ts` gives three pending rows different countdown
  lengths, so a demo can show fresh / mid / about-to-expire without waiting out a full 120 s.

---

## 9. Build status

All nine planned build slices are complete, plus later passes for multi-account support, decision
detail, a visual redesign, and the desktop phone frame.

**Built and working:** sign-in with customer picker · home with hero, pending review, recent
decisions and active policies · bottom tab bar with a live pending badge · the activity feed with
filters and date grouping · decision detail with evidence, injection box and policy links · the
step-up inbox with live countdowns and approve/reject · the new-policy flow on both the AI-read and
manual-form paths · accounts, card detail and spend meters · revoke · a PWA manifest and icon.

**Verified state:** `npm run build` passes clean (type-check included), `npm run lint` passes, and
all five scenario instructions compile and confirm in mock mode.

**Deliberately not built,** each after being considered:

- **An account switcher.** Every account a customer holds now renders generically, so nothing needs
  to focus on one at a time.
- **A tighten / manage screen.** It was built, tried and pulled — the client function and endpoint
  remain dormant, waiting for a design worth shipping.
- **A caching service worker.** `public/sw.js` is an intentional no-op: a demo rebuilds often, and
  stale cached assets would be worse than no offline support.
- **A responsive desktop redesign.** `DeviceFrame` presents the unchanged mobile UI as a phone at
  desktop widths instead, which matches how this app is meant to be embedded.

---

## 10. Known gaps

- **`reason_codes` is carried in the types and fixtures but no screen renders it.** The UI explains
  decisions through `message` + `evidence` instead. If codes are ever shown, they need one shared
  code→label map with a neutral fallback for unknown codes — not per-screen strings.
- **C4 (tighten) has no UI.** Endpoint and client function exist and are dormant.
- **No live event stream yet** — polling every 5 s. The seam for it is `mergeDecisions`.
- **Few tests.** `npm test` runs Node's built-in runner over `tests/` for pure `src/lib/` logic; no
  component tests. `npm run build`, `npm run lint` and `npm test` are the gate.
- **No router and no persistence** — a reload signs the customer out.
- **Limits are parsed out of prose** (§5) rather than sent as structured numbers. It works, and it
  is fragile.

---

## 11. How to add a feature

1. **Need new data?** Add one function to the matching `src/api/*.ts` module, following the
   `VITE_USE_MOCKS` branch pattern — mock branch first, `fetch` branch second, identical signature.
   Add its type to `src/api/types.ts`. Agree the endpoint with the backend before inventing one.
2. **Need a fixture?** Generate it from the data pack with a script under `scripts/`, joined by ID.
   Do not hand-type records, and mark it `mock: true`.
3. **Shared across screens?** Put it in `state/` as a context provider. If it involves incoming
   decisions, it goes through `mergeDecisions` — no exceptions.
4. **New screen?** Add a folder under `src/screens/` and wire the navigation state in `App.tsx`.
5. **Styling?** Use existing tokens. A new token goes in `src/styles/tokens.css` and is mapped in
   `src/index.css` — never a hex value in a component. Icons come from the icon library.
6. **Before handing over:** run `npm run build`, `npm run lint` and `npm test`, check the screen against the
   hard rules in §6 (especially untrusted text, step-up wording, and visible uncertainty), and
   confirm it still works at 390px wide.

# API contract — what the backend must provide

This document is written **from the frontend's side**. It states what the backend has to return,
and how it has to behave, for the wallet control UI to work correctly.

> **Not the binding contract in this repo.** `../docs/api-contract.md` is — it is what
> `backend/oneguard/api/models.py` implements, and its §6 is the only list of changes this UI
> takes. This file is the frontend's own statement of its needs, written before that backend
> existed; where the two differ, `../docs/api-contract.md` wins and this file is the record of
> what the UI actually depends on.

Every requirement here is traceable to real frontend code. Where a requirement looks fussy
(string formats, exact wording, full lists vs. deltas), there is a "why" line naming the file that
depends on it. Those are the ones that break the UI *silently* if they are ignored — no error, no
crash, just a wrong number or a blank section on screen.

**Reading guide for backend engineers:** §1 is the ground rules, §2 is the endpoint list, §3 is the
field-by-field type reference, §4 is the behavioural guarantees — §4 is the part that is easy to
miss and expensive to get wrong. §7 is a checklist you can work through.

---

## 1. Ground rules

### 1.1 Transport

| | |
| --- | --- |
| Base URL | `/api` — **relative**, configured as `VITE_API_BASE_URL` |
| Protocol | HTTP/1.1, JSON |
| Request content type | `application/json` on every request that has a body |
| Response content type | `application/json` (except `204`) |
| Character encoding | UTF-8 |
| Authentication | **None between browser and backend.** See §1.3 |

The base URL is relative on purpose. It means one built bundle works behind a dev-server proxy,
behind the backend's own static file mount, and behind any single-origin deployment, with no
rebuild. Consequence for the backend: **the app and the API are expected to be served from one
origin.** If you ever serve them from different origins you must add CORS, and the frontend needs a
rebuild with an absolute base URL — so prefer single origin.

If the backend serves the built frontend itself, **mount the static files after every API route.**
A catch-all static mount registered first will swallow `/api/...` and every call returns HTML,
which surfaces in the browser as `Unexpected token '<' in JSON`.

### 1.2 Response envelopes

The envelope convention is **not uniform**, and the frontend depends on the exact shape per
endpoint. This is inherited, not ideal — do not "tidy" it without changing the client.

| Style | Endpoints | Shape |
| --- | --- | --- |
| Wrapped in a named key | C12, C6, C10, C3 | `{ "customers": [...] }`, `{ "decisions": [...] }`, `{ "accounts": [...] }`, `{ "mandate": {...} \| null }` |
| Bare object | C1, C2, C4 | The `PolicyDraft` / `Mandate` object at the top level, no wrapper |
| No body | C5, C8 | `204 No Content` |

*Why:* `src/api/*.ts` reads `data.customers`, `data.decisions`, `data.accounts`, `data.mandate`
respectively, and casts C1/C2/C4 responses directly. A wrapper added or removed anywhere here
produces `undefined` and a blank screen.

### 1.3 Secrets

The browser must never receive a Viseca API key, and no `VITE_`-prefixed variable may hold one —
anything with that prefix is compiled into the public bundle. The backend holds the key and is the
only thing that talks to the payment platform. The browser knows exactly one backend: `/api`.

### 1.4 Unknown fields

The frontend reads only the fields documented in §3 and ignores extras, so adding a field is safe
and needs no coordination. **Removing or renaming a field is a breaking change**, as is changing a
field's type or its null-ability.

---

## 2. Endpoints

Nine endpoints. Seven are live in the UI; two (C3, C4) are defined in the client but currently
called from nowhere — see §5.

| # | Method | Path | Body | Success | Called by |
| --- | --- | --- | --- | --- | --- |
| C12 | `GET` | `/api/customers` | — | `200 { customers: Customer[] }` | Sign-in screen, once |
| C6 | `GET` | `/api/customers/{customer_id}/decisions` | — | `200 { decisions: Decision[] }` | Decisions store, **every 5 s** |
| C10 | `GET` | `/api/customers/{customer_id}/accounts` | — | `200 { accounts: Account[] }` | Home, Accounts, New-policy flow |
| C1 | `POST` | `/api/cards/{card_id}/policy-drafts` | `{ instruction }` **or** `{ form }` | `200 PolicyDraft` | New-policy flow |
| C2 | `POST` | `/api/policy-drafts/{draft_id}/confirm` | `{ checks, uncertainty_policy, open_questions }` | `200 Mandate` | New-policy confirm |
| C3 | `GET` | `/api/cards/{card_id}/policy` | — | `200 { mandate: Mandate \| null }` | *nothing yet* |
| C4 | `POST` | `/api/cards/{card_id}/policy/tighten` | `{ add_checks, uncertainty_policy? }` | `200 Mandate` | *nothing yet* |
| C5 | `POST` | `/api/cards/{card_id}/policy/revoke` | — | `204` | Card detail → Revoke |
| C8 | `POST` | `/api/authorizations/{authorization_id}/resolve` | `{ decision }` | `204` | Step-up Approve / Reject |

---

### C12 · `GET /api/customers`

Lists the demo customers shown on the sign-in screen. A UI-only concept; it has no equivalent in
the payment platform's own API.

```json
{
  "customers": [
    { "customer_id": "CU0001", "name": "Alex Meier", "home_region": "Zurich region",
      "card_id": "CA0001", "scenario_id": "SCEN0001", "live": true },
    { "customer_id": "CU0002", "name": "Sofia Keller", "home_region": "Basel region",
      "card_id": null, "scenario_id": null, "live": false }
  ]
}
```

**Requirements**

- Return **all** selectable customers in one response. There is no pagination and no search.
- `live: true` means this customer has a scenario and a card behind them and will produce
  decisions. `live: false` means they are selectable but nothing will happen — `card_id` and
  `scenario_id` must then be `null`, not `""` and not omitted.
- The sign-in screen separates live customers from the rest, so `live` must be accurate.
- Order is not significant; the UI groups them itself.

---

### C6 · `GET /api/customers/{customer_id}/decisions`

**The single most important endpoint.** It feeds the activity feed, the step-up inbox, the home
screen, decision detail, the tab-bar badge, and every spend meter. It is polled every 5 seconds for
as long as the app is open.

```json
{
  "decisions": [
    {
      "authorization_id": "AU0006",
      "customer_id": "CU0001",
      "card_id": "CA0001",
      "decision": "uncertain",
      "uncertain_outcome": "pending",
      "status": "pending_human",
      "reason_codes": ["return_terms_unknown"],
      "message": "This shop doesn't say whether you can return it, so we're asking you first.",
      "uncertainty": { "note": "The return window was not stated by the shop." },
      "occurred_at": "2026-07-14T10:05:00Z",
      "merchant": { "merchant_id": "ME0013", "name": "Alpine Outdoor AG" },
      "amount": 118.40,
      "currency": "CHF",
      "billing_amount_chf": 118.40,
      "items": [
        { "item_name": "Trail jacket", "quantity": 1, "unit_price": 109.00,
          "currency": "CHF", "item_details": "Colour: slate. Returns: see website." }
      ],
      "injection_flag": null,
      "evidence": [
        { "rule": "Total at or below CHF 120 per order", "outcome": "pass",
          "detail": "CHF 118.40 of CHF 120.00" },
        { "rule": "Returnable within 14 days", "outcome": "uncertain",
          "detail": "The shop did not state a return window." }
      ],
      "order_returnable": "unknown",
      "delivery_by": "2026-07-17T00:00:00Z",
      "deadline_at": "2026-09-24T09:31:00Z"
    }
  ]
}
```

**Requirements**

1. **Return the customer's complete decision history on every call — never a delta, never a page.**
   The client folds each response into what it already holds and treats it as the full picture.
   *Why:* `src/state/mergeDecisions.ts`. Sending only new rows works by accident today (anything
   missing is kept rather than dropped) but any future change to that merge assumes a full list.
2. **Filter to the requested customer.** The client does not filter by `customer_id` in live mode.
   Returning another customer's decisions leaks them into this customer's feed and spend totals.
3. **`authorization_id` is the identity key**: unique per purchase, and stable for the life of that
   purchase. The merge, the resolve call, and every "is this the same purchase" comparison use it.
   A purchase whose id changes between polls appears twice in the feed and double-counts in spend.
4. **Newly decided purchases must appear within one poll** (5 s) of being decided, or the feed and
   the badge go stale while the customer is watching.
5. See §4 for the field-level invariants — the consistency rules between `decision`,
   `uncertain_outcome` and `status`, the timestamp format, and the wording contract for `evidence`.

---

### C10 · `GET /api/customers/{customer_id}/accounts`

Accounts with their cards nested. Drives the Accounts screen, the home screen's card list, and the
card picker in the new-policy flow.

```json
{
  "accounts": [
    {
      "account_id": "AC0001",
      "customer_id": "CU0001",
      "account_type": "debit",
      "account_purpose": "daily_spending",
      "status": "active",
      "per_transaction_limit_chf": 1200.0,
      "monthly_limit_chf": 4500.0,
      "cards": [
        { "card_id": "CA0001", "card_type": "debit", "card_purpose": "everyday", "status": "active" },
        { "card_id": "CA0002", "card_type": "debit", "card_purpose": "mobile_and_online", "status": "active" }
      ]
    }
  ]
}
```

**Requirements**

- Return **every** account the customer holds, each with **every** card. Customers with more than
  one account and more than one card both exist in the data and both render.
- `cards` must be present and an array. An account with no cards sends `[]`, never `null` and never
  an omitted key.
- `per_transaction_limit_chf` and `monthly_limit_chf` are the **bank's own** limits. They are
  fetched for completeness and deliberately never drawn on the spend meter, which shows the
  customer's own policy limit instead. Do not merge the two concepts.
- A failed call here is non-fatal — the new-policy flow just offers no sibling cards to pick from.

---

### C1 · `POST /api/cards/{card_id}/policy-drafts`

Turns what the customer said (or filled in) into a **proposed** policy for them to review. This is
a draft: it grants nothing until C2 confirms it.

Two mutually exclusive request bodies. The backend must accept both.

```json
{ "instruction": "Groceries only, up to CHF 120 per order and CHF 300 per week." }
```

```json
{ "form": { "per_order_limit_chf": 120, "period_limit_chf": 300, "period_days": 7,
            "categories": ["Groceries"], "sellers_used_before_only": true,
            "uncertainty_policy": "ask" } }
```

Responds with a bare `PolicyDraft` (§3.5).

**Requirements**

- **`draft_id` must be usable as a URL path segment** and must remain valid until confirmed.
- **`dry_run` is required**, including on the form path. The review screen renders its counts and
  its `insight` sentence. If there is genuinely no history to test against, return
  `sample_size: 0`, zeros, and an `insight` that says so in plain language — not `null`.
- **`checks[].text` must follow the wording contract in §4.4** or the customer's limits will not
  appear on the spend meter.
- **Never invent a limit the customer did not state.** A fragment that cannot be read must surface
  as an `open_questions` entry — never silently dropped. A policy the customer confirms believing
  it has a limit it does not have is the worst failure this system can produce.
- `uncertainty_policy` on a draft is `"ask"` or `"decline"` only. `"ask"` is the expected default.
  The UI offers no control that proposes `"approve"`.
- Latency: the UI shows a "reading your instruction" screen during this call, so a second or two is
  fine. A failure or a timeout drops the customer onto a recovery screen offering a retry or the
  manual form — so **failing is acceptable, hanging forever is not.** Bound this call server-side.

---

### C2 · `POST /api/policy-drafts/{draft_id}/confirm`

The customer has read the draft and agreed. This is the moment permission is granted.

```json
{ "checks": [ ... ], "uncertainty_policy": "ask", "open_questions": [ ... ] }
```

Responds with a bare `Mandate` (§3.6).

**Requirements**

- **The body is deliberately not empty**, unlike the payment platform's own bodyless mandate
  confirm. The customer can change `uncertainty_policy` on the review screen before confirming, so
  the frontend sends back the draft content as the customer actually approved it. Our `/api` is a
  wrapper, not a 1:1 proxy — doing the re-draft-then-confirm dance with the platform is the
  backend's job and must stay invisible to the UI.
- The returned `Mandate` must carry `status: "active"` and a real `confirmed_at`. The UI stores this
  object as the card's live policy for the session and renders it immediately, without re-reading.
- Confirming twice with the same `draft_id` should not create two mandates.

---

### C3 · `GET /api/cards/{card_id}/policy` *(dormant)*

Returns `{ "mandate": Mandate | null }` — `null`, not `404`, when the card has no policy.
**No screen calls this today** (see §5). Implement it when policy state needs to survive a reload.

---

### C4 · `POST /api/cards/{card_id}/policy/tighten` *(dormant)*

```json
{ "add_checks": [ ... ], "uncertainty_policy": "decline" }
```

**No screen calls this today** (see §5). When it is built, the rule it must enforce is absolute:
**tighten only.** Existing checks are never removed, replaced or weakened; `uncertainty_policy` may
move toward `decline` but never back toward `ask` or `approve`. Reject anything else server-side
rather than trusting the client — loosening is a new policy the customer must confirm explicitly.

---

### C5 · `POST /api/cards/{card_id}/policy/revoke`

No request body. Responds `204`.

**Requirements**

- Revokes **the policy only.** It must not decide, cancel or alter any purchase already queued or
  already waiting on the customer — what a revoke does to work in flight is unspecified by the
  platform, and the UI will show a pending purchase as cancelled **only** once the platform
  confirms a cancellation.
- A revoked policy must keep existing as a revoked record, not be deleted. Past decisions link back
  to the policy that produced them, and "never had a policy" must stay distinguishable from "had
  one, revoked it" when the customer reviews old activity.

---

### C8 · `POST /api/authorizations/{authorization_id}/resolve`

The customer's answer to a step-up. Responds `204`.

```json
{ "decision": "approve" }
```

`decision` is `"approve"` or `"decline"` — and note the asymmetry: the customer's *answer*
vocabulary is approve/decline, while the *decision category* the UI displays is
approved/stopped/uncertain (§4.1).

**Requirements**

1. **Called only by a customer's explicit tap**, once per purchase. The frontend never calls it
   automatically, never retries it, and never calls it for a step-up that expired.
2. **A window that closed unanswered must be refused** — respond `4xx` (`409` is the sensible
   choice), and do not record an answer. Nobody answered; recording that nobody answered is correct,
   inventing an answer is not.
3. **Closing a lapsed window must be durable.** Expire lapsed step-ups server-side (closing them
   when the feed is read is enough) so the expiry survives a page reload. The UI also expires them
   locally for immediacy, but local state dies with the tab.
4. A failure here is shown to the customer as an inline error on the pending card, and the step-up
   stays pending so they can try again. So a transient `5xx` is recoverable; a wrong `2xx` is not.

---

## 3. Types

Field-by-field. "Required" means the key must be present; a nullable field must still be present
with an explicit `null`.

### 3.1 `Customer`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `customer_id` | `string` | yes | `CU…` |
| `name` | `string` | yes | Display name |
| `home_region` | `string` | yes | e.g. `"Zurich region"` |
| `card_id` | `string \| null` | yes | `null` when `live` is false |
| `scenario_id` | `string \| null` | yes | `null` when `live` is false |
| `live` | `boolean` | yes | Will this customer actually produce decisions |

### 3.2 `Account` and `Card`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `account_id` | `string` | yes | `AC…` |
| `customer_id` | `string` | yes | |
| `account_type` | `string` | yes | e.g. `"debit"` |
| `account_purpose` | `string` | yes | e.g. `"daily_spending"` |
| `status` | `string` | yes | e.g. `"active"` |
| `per_transaction_limit_chf` | `number` | yes | Bank limit — never shown on the meter |
| `monthly_limit_chf` | `number` | yes | Bank limit — never shown on the meter |
| `cards` | `Card[]` | yes | `[]` if none; never `null` |

`Card`: `card_id`, `card_type`, `card_purpose`, `status` — all `string`, all required.

### 3.3 `Decision`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `authorization_id` | `string` | yes | Identity and merge key. Unique, stable |
| `customer_id` | `string` | yes | |
| `card_id` | `string` | yes | Groups spend and card-scoped activity |
| `decision` | `"approved" \| "stopped" \| "uncertain"` | yes | Three categories only — §4.1 |
| `uncertain_outcome` | `"pending" \| "expired" \| "approved" \| "declined" \| null` | yes | Non-null **only** when `decision` is `"uncertain"` |
| `status` | `"final" \| "pending_human"` | yes | `pending_human` = still waiting on the customer |
| `reason_codes` | `string[]` | yes | Vocabulary in §4.5. May be `[]` |
| `message` | `string` | yes | One plain sentence, naming the actual number |
| `uncertainty` | `{ note: string } \| null` | yes | Explicit `null` when certain |
| `occurred_at` | `string` | yes | **Simulated** scenario time, ISO-8601 UTC — §4.2 |
| `merchant` | `{ merchant_id, name }` | yes | `name` is untrusted text — §4.6 |
| `amount` | `number` | yes | **Already includes delivery.** Never add it again |
| `currency` | `string` | yes | `CHF`, `EUR`, `GBP`, `USD` |
| `billing_amount_chf` | `number` | yes | CHF equivalent; what every total is summed from |
| `items` | `DecisionItem[]` | yes | May be `[]` |
| `injection_flag` | `{ flagged: true, reason: string } \| null` | yes | Backend-detected only — §4.6 |
| `evidence` | `EvidenceItem[]` | yes | **Must not be empty** — §4.3 |
| `order_returnable` | `"true" \| "false" \| "unknown" \| "not_applicable"` | yes | A **string** enum, not a boolean — §4.7 |
| `delivery_by` | `string \| null` | yes | ISO-8601 UTC, or explicit `null` |
| `deadline_at` | `string` | only when `status` is `pending_human` | **Real** clock, ISO-8601 UTC — §4.2 |

`DecisionItem`: `item_name` (`string`), `quantity` (`number`), `unit_price` (`number`),
`currency` (`string`), `item_details` (`string`, untrusted).

`EvidenceItem`: `rule` (`string`, plain language), `outcome` (`"pass" | "fail" | "uncertain"`),
`detail` (`string`, should name the concrete number or fact).

### 3.4 `RuleCheck`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | `string` | yes | Stable within a draft/mandate |
| `text` | `string` | yes | Plain language, rendered verbatim — **wording contract in §4.4** |
| `source` | `"exact" \| "inferred"` | yes | `exact` = the customer's own wording; `inferred` = our reading |
| `uncertainty` | `string \| null` | yes | Shown as a caveat under the check |

The UI renders `text` and never constructs or displays a machine rule — no field names, no
operators. Translating a hard rule into a sentence is the backend's job.

### 3.5 `PolicyDraft`

`draft_id`, `card_id`, `instruction` (the customer's own words; `""` on the form path),
`checks: RuleCheck[]`, `uncertainty_policy: "ask" | "decline"`, `open_questions: string[]`,
`dry_run: DryRunResult` — all required.

`DryRunResult`: `sample_size`, `would_violate`, `would_fit`, `would_ask` (all `number`) and
`insight` (`string`).

### 3.6 `Mandate`

`mandate_id`, `card_id`, `instruction`, `checks: RuleCheck[]`,
`uncertainty_policy: "ask" | "decline" | "approve"`, `open_questions: string[]`,
`status: "active" | "revoked"`, `confirmed_at` (ISO-8601 UTC) — all required.

### 3.7 `FormInput` (request only)

`per_order_limit_chf: number|null`, `period_limit_chf: number|null`, `period_days: 7|14|30|null`,
`categories: string[]`, `sellers_used_before_only: boolean`,
`uncertainty_policy: "ask" | "decline"`.

---

## 4. Behavioural guarantees

This is the section that matters. Everything above is shape; everything here is meaning. These are
the rules that, when broken, produce a UI that looks fine and is wrong.

### 4.1 The three-category vocabulary, and the consistency matrix

The engine decides `approve` / `decline` / `step_up`. The API surfaces **three** customer-facing
categories, and the mapping is the backend's job — the frontend never computes an outcome:

| Engine | `decision` | Meaning to the customer |
| --- | --- | --- |
| `approve` | `"approved"` | It went through |
| `decline` | `"stopped"` | We stopped it |
| `step_up` | `"uncertain"` | We weren't sure, so a person was involved |

**A purchase that was ever uncertain stays `"uncertain"` forever.** What the customer answered, or
that nobody answered, is recorded in `uncertain_outcome` — never by reclassifying the decision.
This is deliberate: the history has to keep showing which purchases needed a person.

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

### 4.2 Timestamps: two clocks, one format

There are two different clocks in this system and mixing them up produces wrong dates on screen.

- **`occurred_at` and `delivery_by` are simulated scenario time.** These are demo/historical dates.
  Never substitute the real clock for them.
- **`deadline_at` is the real wall clock.** It is the only real-time value, because the customer's
  120-second answer window is a real countdown.

**Format requirement: ISO-8601, UTC, `Z` suffix, fixed width** — `2026-07-14T10:05:00Z`.

*Why this is strict:* the frontend orders decisions and computes rolling spend windows by comparing
these timestamps **as strings** (`src/lib/spend.ts`), and renders dates with UTC accessors
(`src/lib/datetime.ts`). Mixed formats — some with a `+02:00` offset, some without a zone, some
with fractional seconds and some without — still parse, but sort wrongly. The failure mode is a
spend total that is quietly too low and an activity feed grouped under the wrong day. Nothing
throws.

**`deadline_at` must not change between polls** for the same purchase. The client keeps the first
value it saw and ignores later ones, precisely so a re-sent value cannot make the countdown stutter
— but the two will then disagree. Send it once, send it stable.

### 4.3 Every decision is explained

`message` and `evidence` are not decoration; they are the product. A decision the customer cannot
understand is a failed decision.

- `evidence` **must not be empty.** Every decision renders its evidence rows; an empty array is an
  unexplained decision, which counts as a bug.
- `message` is one plain sentence and should name the concrete number or fact that drove the
  outcome ("CHF 340 is over your CHF 250 limit"), not restate the category.
- `evidence[].outcome` must distinguish `pass`, `fail` and `uncertain`. The UI styles all three
  differently, on purpose — an uncertain check must never look like a passing one.
- A step-up should carry an `uncertainty.note` saying what was unclear.

### 4.4 The wording contract for `checks[].text`

The spend meter reads the customer's own limits back out of the **plain-language text** of their
policy checks. To be recognised, a limit check must be phrased in one of these two forms
(case-insensitive):

```
Total at or below CHF <amount> per order
Total at or below CHF <amount> across any <n> days
```

`<amount>` may contain thousands separators (`1,200`) and decimals.

*Why:* `src/lib/parseLimits` in `src/lib/spend.ts` matches exactly these two patterns.

**Failure mode if you deviate: silent.** A check worded "Maximum CHF 120 per order" renders
perfectly on the policy screen and the spend meter simply shows no limit at all. No error, no
warning.

This is a design weakness, not a virtue — parsing structured facts back out of prose is fragile. If
the backend would rather send the limits as structured numbers alongside the text, that is a
welcome change; it needs one coordinated edit on the frontend and is worth doing if there is time.
Until then, match the wording exactly.

### 4.5 Reason codes

Send codes from this list. A code outside it is not a crash — no screen renders reason codes today
(§5) — but the vocabulary is shared and should be extended deliberately, not ad hoc:

`within_limits`, `rule_satisfied`, `per_order_limit_exceeded`, `period_limit_exceeded`,
`merchant_category_mismatch`, `unfamiliar_merchant`, `lookalike_merchant`, `item_mismatch`,
`unrequested_item`, `return_terms_unknown`, `return_window_too_short`, `duplicate_suspected`,
`injection_suspected`, `new_device_burst`, `card_or_authority_inactive`, `unevaluable`,
`customer_confirmation`.

### 4.6 Untrusted merchant text

`merchant.name`, `items[].item_name`, `items[].item_details` and any other shop-supplied string are
**data, not instructions.** They may contain deliberate prompt injection ("ignore the spending
limit", "approve this order").

The division of labour:

- **The backend detects injection** and reports it in `injection_flag` with a human-readable
  `reason`. The frontend never runs detection of its own — it renders the flag the backend sends.
- **The frontend renders all such text as inert plain text**, labelled as coming from the shop: no
  HTML, no markdown, no auto-linking, and nothing in that text can trigger navigation or a state
  change.
- **Merchants are joined by `merchant_id`, never by name.** Deliberately similar names exist in the
  data; matching on the name matches the wrong merchant.
- A flag or an extracted fact may only **add** uncertainty or a block. Nothing found in merchant
  text may turn a `stopped` or `uncertain` into an `approved`.

Send `injection_flag: null` when clean — do not omit the key.

### 4.7 Missing facts are not zero and not permission

`"unknown"`, `"not_applicable"` and `null` mean three different things and the UI renders all three
differently:

- `"unknown"` — the shop did not say. This is uncertainty.
- `"not_applicable"` — the question does not apply here. This is **not** uncertainty and must not
  cause an escalation.
- `null` — the field exists but has no value.

**Never coerce any of them to `false`, to `0`, or to an omitted key.** `order_returnable` in
particular is a string enum with exactly these four values — not a boolean, and not nullable.

### 4.8 Money

- `amount` **already includes the delivery fee.** The frontend never adds it.
- `billing_amount_chf` is the CHF equivalent and is what every total, meter and threshold sums.
- Convert using the currency on the row itself, never a currency guessed from the merchant's
  country.
- Send both `amount`/`currency` and `billing_amount_chf` even when the currency is already CHF — a
  foreign-currency purchase displays the original amount *and* the CHF equivalent.

### 4.9 Spend counting

Only **final approvals** count toward spend. A pending step-up does not count (the UI previews it
separately as a dashed "if you approve this" ghost), and a declined purchase never counts.

The client computes spend from the C6 feed rather than asking for a total, so there is no spend
endpoint to build. What this requires from the backend is simply that `billing_amount_chf`,
`card_id`, `decision` and `occurred_at` are correct on every row.

### 4.10 The 120-second window

The customer's answer window is 120 seconds. The frontend keeps that number in one config constant
and treats `deadline_at` as authoritative per purchase. If the platform's real window ever differs,
send a `deadline_at` that reflects it — do not assume the frontend's constant.

---

## 5. What the frontend does *not* need

Do not build these for the UI's sake:

- **A spend or totals endpoint.** Computed client-side from C6 (§4.9).
- **A separate step-up / approvals endpoint.** A pending step-up is just a `Decision` with
  `status: "pending_human"`; the inbox filters the C6 feed. There is no `Approval` type.
- **A decision-detail endpoint.** Detail renders from the already-loaded C6 row.
- **Scenario-run operator endpoints.** Starting or restarting a run is an operator mechanism the
  customer never sees; the UI neither calls it nor should. If such endpoints exist, they are
  outside this contract.
- **Rich error bodies.** The client checks the HTTP status and shows its own copy; it never parses
  an error payload. Correct status codes matter, prose in the body does not.
- **Pagination, sorting or filtering parameters.** Every list endpoint returns everything and the UI
  sorts and filters locally.

**Currently dormant:** C3 (`getPolicy`) and C4 (`tightenPolicy`) are implemented in the client and
called from nowhere. Policy state lives in memory for the session, so a reload signs the customer
out and forgets it — wiring C3 is what would fix that. C4 has no UI because the tighten screen was
built and then pulled. Both endpoints should still behave as specified if built, but neither is on
the critical path today.

**Proposed, not built:** a server-sent-events stream for live decisions, as an additive complement
to C6 polling. The client already funnels every incoming decision through one merge function that
knows nothing about transport, so adding a stream later touches no screen.

---

## 6. Error handling

What the frontend actually does with a non-2xx response:

| Endpoint | On failure |
| --- | --- |
| C12 (customers) | Sign-in shows an error state; nothing is selectable |
| C6 (decisions) — **first** read | Error state with a retry control |
| C6 (decisions) — **a refresh** | **Keeps what is on screen.** Those decisions are still true, just not fresh. No error shown |
| C10 (accounts) | Card picker silently offers no sibling cards; the flow continues |
| C1 (compile) | Instruction path → recovery screen (retry, or switch to the manual form). Form path → inline error |
| C2 (confirm) | Inline error on the review screen; the draft is kept so the customer can retry |
| C5 (revoke) | Inline error; the policy stays as it was |
| C8 (resolve) | Inline error on the pending card; the step-up **stays pending** and can be retried |

Two consequences for the backend:

1. **Never return `2xx` for work that did not happen.** The UI takes a `2xx` as truth and updates
   immediately — an optimistic update on a lie is unrecoverable, where an honest `5xx` is just a
   retry.
2. **Prefer a fast failure to a hang.** Every one of these paths recovers gracefully from an error
   and none of them recovers from a request that never returns. Bound your calls server-side.

Suggested status codes (the UI treats all non-2xx alike, so these are for the backend's own
hygiene): `404` unknown customer / card / draft / authorization; `422` a request body that is
neither `instruction` nor `form`; `409` a tighten that is not a pure addition, or a resolve for a
step-up that is not awaiting an answer — **including one whose window already closed**.

---

## 7. Backend checklist

Shape:

- [ ] All nine endpoints on `/api`, served from the same origin as the app
- [ ] Static file mount registered **after** every API route
- [ ] Envelope per endpoint exactly as §1.2 (wrapped vs. bare vs. `204`)
- [ ] Every documented key present; nullable keys present with explicit `null`
- [ ] `cards`, `items`, `evidence`, `reason_codes`, `open_questions` are arrays, never `null`

Correctness:

- [ ] `decision` / `uncertain_outcome` / `status` only ever in a combination from §4.1's matrix
- [ ] `deadline_at` present on every `pending_human` row, stable across polls
- [ ] Every timestamp ISO-8601 UTC with `Z`, fixed width, no mixed offsets
- [ ] `occurred_at` is simulated time; `deadline_at` is the real clock
- [ ] `evidence` non-empty on every decision, `message` names the actual number
- [ ] Limit checks worded exactly as §4.4, or the meter shows nothing
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

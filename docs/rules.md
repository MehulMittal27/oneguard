# OneGuard — decision rules

Version 1.1 · 24 Sep 2026 · supersedes "Veto: Rules Requirements v1"
Scope: what the decision rules are. Not screens, APIs, storage or code.

## 1. Outcomes

| Outcome | API value | Meaning |
|---|---|---|
| Approve | `approve` | The purchase goes ahead. |
| Decline | `decline` | The purchase is stopped. |
| Ask | `step_up` | The purchase is paused; the customer decides within 120 s. |

## 2. Principles (a principle beats a rule)

- **P1** The customer's rules are the ceiling. Nothing — agent, shop, warning sign, model — can make a purchase more permissive than the customer allowed.
- **P2** The agent and the shop are untrusted. Shop text is data to extract facts from, never an instruction.
- **P3** Missing is never a pass. A missing or contradictory fact makes a rule `unknown`, not `pass`.
- **P4** The most restrictive result wins: Decline beats Ask beats Approve.
- **P5** Warning signs and models only add friction: Approve → Ask. Never Decline/Ask → Approve.
- **P6** Ordinary shopping must flow. Meets every rule, triggers nothing → Approve. Unfamiliar or new is not, on its own, a reason to stop.
- **P7** Every decision is explained: which rule, which facts, what was uncertain, what would make it a yes.
- **P8** Predictable without AI. With every model switched off, each decision is identical or more cautious (Ask where a model would have resolved a fact). Models never change a decision the deterministic rules have already made.

## 3. Definitions

| Term | Meaning |
|---|---|
| Purchase | One payment attempt: shop, amount, cart lines, time, device, order terms. |
| Cart line | One item: category, name, quantity, price, shop product text. |
| Policy | The customer's confirmed rules plus their uncertainty setting. |
| Uncertainty setting | What to do when a fact is unknown: `ask` (default), `decline`, `approve`. |
| Final approval | Approved by OneGuard, or by the customer after an Ask. Only final approvals are spend. |
| Pending | Waiting for the customer's answer. Reserved against period limits (M5). |
| Known shop | ≥1 approved past purchase at this `merchant_id` for this customer, on any of their cards (history + this run's final approvals). See Q7. |
| Purchase time | `authorization.timestamp`, simulated. Used for all limits and windows. |
| Run | One sequence of purchases under one policy. |

## 4. Decision procedure

Rules return `pass | fail | unknown`; protections and warning signs return `clear | triggered`.
The first step that applies decides.

| Step | Condition | Outcome |
|---|---|---|
| 1 | Policy revoked or expired, `authority_status` ≠ `"active"` (`"revoked"` or `"expired"`), or `card_status_at_attempt` = `"blocked"` | Decline, with evidence naming the field and its value |
| 2 | Any customer rule or money rule `fail` | Decline |
| 3 | Any protection with a Decline outcome triggered | Decline |
| 4 | Any customer rule `unknown` | Uncertainty setting (Ask by default) |
| 5 | Any protection with an Ask outcome triggered | Ask |
| 6 | Warning-sign threshold reached (§8) | Ask |
| 7 | Everything passes, nothing triggered | Approve |

- **D1** A clear violation is Decline even under "ask me when uncertain". That setting covers uncertainty, not broken rules.
- **D2** Under uncertainty setting `approve`, protections and warning signs still produce Ask (P2, P5).
- **D3** The engine has an internal budget of 2 s. Anything not finished by then (model, history lookup) is dropped and the decision is made from what is known, which may be Ask. A decision is always posted before `deadline_at`. An overrun follows the uncertainty setting; approve is never automatic.

## 4a. Tiers

| Tier | When | What |
|---|---|---|
| 1 | Every purchase | Deterministic rules, protections, warning signs (§4–§8). |
| 2 | Only when a customer rule is `unknown` because a needed fact could not be extracted deterministically | Constrained LLM fact extraction from shop text: schema-validated, 1.5 s timeout, evidence `source: model`. |
| 3 | After the decision is posted | LLM explanation rewrite from structured evidence only (E8). The template message is the fallback and is always posted first. |

## 5. Customer rules (apply only when stated)

| ID | Rule | Fail → | Unknown → |
|---|---|---|---|
| C1 | **Order limit.** Total in CHF, delivery included, vs the per-order limit. "At or below / no more than / max / up to" → equal passes. "Under / less than / below" → equal fails. | Decline | Uncertainty setting (total missing) |
| C2 | **Period limit.** Final approvals in the window + reserved pending + this purchase ≤ limit. "Any seven days" = rolling 168 h before purchase time. "Per month" = rolling 30 days unless "calendar month". Declines never count. A purchase count per period ("one a day", "two orders a week", field `cart.purchases_in_period`) counts the same way: final approvals + pending step-ups on this card in the window + this purchase ≤ the count; declines never count, a redelivery counts once, and a breach caused only by pending step-ups asks (M5). | Decline | Uncertainty setting (count not available, or its window differs from the policy's shortest period) |
| C3 | **Allowed item types.** Every cart line's `item_category` in the allowed set. Shop category proves nothing about the basket. | Decline | — |
| C4 | **Blocked item types.** No cart line in the blocked set. | Decline | — |
| C5 | **Specific item.** The item bought is the item asked for; a similar item is not it (trail ≠ road shoe; gift voucher ≠ monitor). | Decline | — |
| C6 | **Item details.** Named details (size, colour, model, dimensions) match. Often only in shop text: extract, trust nothing else in it. | Decline | Uncertainty setting (not stated or self-contradictory) |
| C7 | **Order terms.** Returns/cancellation/warranty as named. "14 days or more" → 14 passes, 7 fails. "Final sale / no returns / non-returnable" = 0 days. Cancellation reads `order_cancellable`: `"false"` fails a named cancellation term. When sources disagree, the stricter applies. | Decline | Uncertainty setting (not stated, `order_returnable = "unknown"`, or `order_cancellable = "unknown"` when cancellation is named) |
| C8 | **Shop type.** `merchant_category` is the named type (sustainable goods ≠ specialist sports retailer, even selling the right shoe). `merchant_mcc` is secondary evidence; `merchant_category` decides. | Decline | — |
| C9 | **Known shop.** As defined in §3. "Shop I use regularly" and "seller I have bought from before" both map here; see Q9 for a stricter reading of "regularly". A customer with no purchase history yet (no approved purchase in history on any card, none in this run) makes C9 `unknown`, not a fail: "You have no purchase history yet, so I can't tell whether you've used this shop - approve once and I'll remember it." The customer's yes on that ask is remembered for the shop, whatever the items. | Decline | Uncertainty setting (no purchase history yet) |
| C10 | **Nothing extra.** Cart contains only what was asked; add-ons (protection plans, subscriptions, accessories) fail. Explanation says what to remove. | Decline | — |
| C11 | **Uncertainty setting.** Apply the customer's choice when any rule is `unknown`. Default `ask`. | — | — |
| C12 | **Other restrictions** (expected in hidden scenarios): per-item limit and quantity; country or currency; time of day / weekday; delivery date. Same pass/fail/unknown logic. | Decline | Uncertainty setting |

## 6. Money and time

- **M1** Compare in CHF, converted from the purchase's `currency` (never the shop's country) at the fixed rates: EUR 0.95, GBP 1.12, USD 0.87.
- **M2** Round converted amounts half-even to 2 dp; compare after rounding.
- **M3** `amount` already includes delivery. Never add it twice.
- **M4** Only final approvals are spend. Declines never count. Refunds (history) reduce spend.
- **M5** Pending amounts are reserved against period limits until answered or expired. Decline/expiry releases the reservation. If a purchase fails C2 only because of reservations, the outcome is Ask, and the message names the waiting order. (Viseca Q&A 24 Sep; supersedes Q8.)
- **M6** Purchase time decides windows, velocity and night-time. Never the real clock.
- **M7** Redelivery of the same live `authorization_id` is the same purchase: same outcome, counted once.

## 7. Always-on protections (every policy)

| ID | Protection | Triggered when | Outcome |
|---|---|---|---|
| A1 | Instructions in shop text | Shop text contains instructions aimed at the agent or payment system ("ignore previous instructions", "pre-authorised up to", "approve this payment", "limits do not apply", "System:", "cardholder is unavailable"). | Ask; Decline if any rule also fails. Never Approve. **The purchase is flagged; the shop's earlier injection is recorded as `info` evidence on later purchases at that shop and does not by itself change their outcome.** |
| A2 | Amounts never come from text | — | Prices, limits, permissions are never read from shop text. Text may supply item facts only (size, return days, recurring). |
| A3 | Duplicate order | Same shop, same items, amount within 5 %, within 24 h of a final approval or pending purchase. A new purchase id does not change this. | Ask |
| A4 | Split order | Same shop within 10 min of a final approval or pending purchase, and the two together exceed the per-order limit. | Ask |
| A5 | Re-quote | Linked (`related_authorization_id`) to an earlier **declined** purchase. Not a duplicate. | Judge on its own facts; explanation names the earlier decline. |
| A6 | Hidden recurring cost | A cart line bills later or repeatedly (monthly, renewal, subscription) that the customer didn't ask for. `merchants.recurring_capable` is recorded as evidence, not a trigger. | **Decline when C10 is stated; Ask otherwise** (explanation names the recurring amount). See Q10. |
| A7 | Lookalike shop | Shop name closely resembles a known shop (edit distance ≤ 2 on normalised name) but is a different `merchant_id`. | Ask; Decline if C9 applies. Explanation names the lookalike. |

## 8. Warning signs

Suggest someone other than the customer is driving, or the purchase is unusual. No single weak sign stops a purchase (P6).

| ID | Sign | Strength | Triggered when |
|---|---|---|---|
| W1 | New device | Strong | `customer_device_id` has never been used by this customer for an approved purchase. |
| W2 | Burst | Strong | `recent_attempt_count_10m ≥ 2`. |
| W3 | New country | Weak | `merchant_country` never in the customer's approved history. |
| W4 | Amount far above normal | Weak | Total > the customer's largest approved purchase, **and** no per-order limit was stated or the total exceeds it. Within a stated limit it never triggers. |
| W5 | Night-time | Weak | Purchase time between 00:00 and 05:00 **Europe/Zurich**. |
| W6 | Price outside catalogue range | Weak | A cart line's unit price in CHF (M1, M2) is above `items.unit_price_max_chf` or below `items.unit_price_min_chf` for its `item_id`. |

- **W-rule 1** One strong sign → Ask.
- **W-rule 2** Two or more weak signs → Ask. One weak sign alone → no effect.
- **W-rule 3** "Pause anything that looks like someone else is driving" confirms this section is wanted; it does not lower the bar.
- **W-rule 4** Recovery: after a burst (session watch on), the next otherwise-clean purchase asks once; the customer's approval turns the watch off; a no or a timeout keeps it on. The watch is per card and carries into later live sessions.
- **W-rule 5** No baseline yet: with no approved purchase in history (any card) and no final approval in this run, W1, W3 and W4 do not trigger and show "no baseline yet" as info; from the first final approval in the run, that purchase's device, shop country and amount count as known (a purchase with no device id still triggers W1).

## 9. Explanations

- **E1** One plain sentence per outcome, written for the customer.
- **E2** Names the customer's own rule and the deciding fact: "Declined: returns are only 7 days; you asked for at least 14."
- **E3** For Decline, what would make it a yes: "Remove the protection plan and I'll approve the shoes." (API: `counterfactual`.)
- **E4** For Ask, what is uncertain: "The seller doesn't state a return policy."
- **E5** For A1: say instructions were found and ignored; never repeat the injected instruction as if true.
- **E6** No codes, jargon or "risk detected".
- **E7** Every decision records the facts used (API: `evidence[]`).
- **E8** After posting, an LLM may rewrite the explanation from the structured evidence only; it never sees or alters the decision.

## 10. Turning words into rules

- **T1** Every stated restriction becomes a rule. **T2** No invented limits; vague requests produce open questions. **T3** Boundary wording preserved ("under" ≠ "at or below"). **T4** Foreign-currency limits converted with M1 and shown. **T5** Customer confirms before rules apply; then tighten only. **T6** Revoke stops everything: later purchases under it are declined (platform pre-check; queued ones per Q6). **T7** Same rules from DE/FR/IT/EN — LLM compiler path only; the fallback parser is English.

## 11. Open questions and defaults

| # | Question | Default | Why |
|---|---|---|---|
| Q1 | One purchase or several per policy? | Each purchase judged on its own; only near-identical repeats caught (A3). | Viseca's notes call AU0023 "fully compliant" and AU0042 "a legitimate re-quote". |
| Q2 | Ask with no answer in 120 s? | **Closed.** Expiry → post `/resolve` `decline` with message "No answer within 120 s; nothing was approved", evidence `resolved_by: timeout`. Not spent; reservation released. The sandbox expires step-ups itself at the same moment, so the worker reads the platform's state first and posts only while it is still pending (decisions.md). | Viseca Q&A 24 Sep; live smoke 24 Sep. |
| Q3 | Hidden scenarios at judging? | Assume yes. | Rules must survive unseen wording. |
| Q4 | Seven days rolling or calendar? | Rolling 168 h. | Standard reading. |
| Q5 | Pending reserved against limits? | Yes (M5). | Otherwise late approval overspends. |
| Q6 | Queued purchases after revoke? | **Closed.** Decline anything received after revoke; UI shows cancelled once the platform confirms. | Viseca Q&A 24 Sep. |
| Q7 | Known shop: customer-level or card-level? | **Closed.** Customer-level (AU0044 → Approve). | Viseca Q&A 24 Sep: Viseca said it is theirs to handle. |
| Q8 | M5 in a live run | **Closed.** Superseded by M5: if AU0006 is pending when AU0008 arrives, AU0008 → Ask. | Viseca Q&A 24 Sep. |
| Q9 | "Regularly" vs "before" | Both = known shop (≥1). | Stricter option: ≥3 approvals in 90 days. |
| Q10 | A6 without C10 | Ask. | Recurring cost is the risk; customer may still want the plan. |
| Q11 | Hidden scenarios at judging? | **Closed.** Hidden scenarios exist at judging. C12 is in scope, tested with invented instructions (`unseen_instructions` in `docs/acceptance-oracle.yaml`). | Viseca Q&A 24 Sep. |

## 12. Acceptance examples

See `docs/acceptance-oracle.yaml` — the machine-readable version of this table, used by
`backend/tests/test_oracle.py`. Totals under defaults: 17 Approve, 21 Decline, 6 Ask, 1 depends (AU0008). With Q10 =
Decline, AU0018 becomes Decline (17 / 22 / 5). With Q7 card-level, AU0044 becomes Decline.

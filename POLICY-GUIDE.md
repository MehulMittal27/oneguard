# OneGuard Policy Guide

Every check OneGuard runs on a purchase, what it does today, and how often it decided the
45 public test purchases. Find how to chnage the policies at the end of this file. 

## Summary: what OneGuard protects

**Of 45 test purchases, we identified that 33 needed a stop or a question. A plain spending limit catches 6 of
them only. OneGuard catches all 33 and still lets the 8 ordinary ones straight through.**

| | Outcome | Purchases | CHF | Meaning |
|---|---|---:|---:|---|
| 🟢 | Approved | 8 | 1,042.55 | no question asked |
| 🔴 | Declined | 20 | 4,378.28 | broke a rule the customer set |
| 🟡 | Asked the customer | 13 | 3,080.40 | the customer decides within 120 s |
| ⚪ | Depends | 4 | 424.50 | on the customer's earlier answer |
| | **Total** | **45** | **8,925.73** | |

```mermaid
pie showData title The 45 public test purchases
    "Approved" : 8
    "Declined" : 20
    "Asked the customer" : 13
    "Depends on an earlier answer" : 4
```

### Problem purchases stopped or asked (out of 33)

| Setup | Stopped or asked | Goes through unchecked |
|---|---|---:|
| No control | 🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴 0 of 33 | CHF 7,458.68 |
| Plain spending limit | 🟢🟢🔴🔴🔴🔴🔴🔴🔴🔴 6 of 33 | CHF 5,732.68 |
| **OneGuard** | 🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢 **33 of 33** | **CHF 0** |

The plain limit only sees the amount. OneGuard still approves 8 of 8 ordinary purchases.

### What a plain spending limit misses (27 purchases, CHF 5,732.68)

| What went wrong | Purchases | CHF |
|---|---:|---:|
| 🔴 Unknown or lookalike shop | 6 | 1,448.28 |
| 🔴 Bought the same item twice | 6 | 1,798.40 |
| 🔴 Wrong item or unwanted extras | 5 | 712.00 |
| 🔴 Bad return terms | 3 | 478.00 |
| 🔴 Duplicate or split order | 2 | 354.00 |
| 🔴 Someone else driving the session | 2 | 260.00 |
| 🔴 Wrong kind of shop | 1 | 189.00 |
| 🔴 Hidden subscription | 1 | 194.00 |
| 🔴 Prompt injection in shop text | 1 | 299.00 |

Outcomes are from our own answer key (`docs/acceptance-oracle.yaml`), written from each
customer's instruction. "Plain spending limit" declines only amounts
over the stated per-order limit. Amounts are `billing_amount_chf` in
`data/purchase_attempts.csv`.

## What decided each purchase

| What decided it | Outcome | Purchases | Which |
|---|---|---:|---|
| C9 known shop | Decline | 6 | shops the customer never used, including the lookalike |
| C1 order limit | Decline | 5 | over the per-order limit (3 of them by 5–8%) |
| C5 requested item | Decline | 3 | trail shoes, a helmet, a gift voucher instead of the item asked for |
| C7 order terms | Decline | 2 | returns shorter than 14 days, or none |
| C3, C6, C8, C10 | Decline | 4 | wrong item type, wrong size, wrong shop type, an add-on |
| A8 already bought | Ask | 6 | AU0019, AU0023, AU0038, AU0042, AU0044, AU0045 |
| A1, A3, A4, A6 | Ask | 4 | injection, duplicate, split order, hidden recurring charge |
| C7 terms unknown, W1 new device, session watch | Ask | 3 | return policy not stated, first use of a device, the first clean purchase after a burst |

> **Doc mismatch to fix:** `docs/rules.md` §12 still says "10 Approve, 20 Decline, 11 Ask,
> 4 depends", but `docs/acceptance-oracle.yaml`, which the tests check against, says
> 8 / 20 / 13 / 4. The oracle is the current one.

## Every check, and what it does today

The engine goes in a fixed order and the first step that applies decides:

1. Card and permission active.
2. Any broken rule declines.
3. A protection that declines.
4. Any unknown fact asks (the customer's "when unsure" setting).
5. A protection that asks.
6. Warning signs.
7. Approve.

"Public 45" counts purchases where that check was the deciding one.

### Customer rules (only when the instruction states them)

| ID | Check | Today | If a fact is missing | Public 45 |
|---|---|---|---|---:|
| C1 | Order limit. Total in CHF with delivery. "at or below / max / up to": equal passes. "under": equal fails. Also "same price as last time". | Decline any amount over the limit | Ask | 5 |
| C2 | Period limit. Rolling window ("any 7 days" = 168 h). Also purchase counts ("one a day"). | Decline; Ask when only waiting orders push it over | Ask | 0 |
| C3 | Allowed item types. Every cart line must be in the allowed set. | Decline | — | 1 |
| C4 | Blocked item types, including "no alcohol" read per line. | Decline | Ask (unclear drinks) | 0 |
| C5 | Specific item. A similar item is not it (trail ≠ road shoe). | Decline | — | 3 |
| C6 | Item details: size, colour, model. | Decline | Ask | 1 |
| C7 | Order terms: return window, cancellation. "Final sale" = 0 days. | Decline | Ask (policy not stated) | 2 + 1 ask |
| C8 | Shop type ("specialist sports retailer"). | Decline | — | 1 |
| C9 | Known shop: at least one approved purchase there by this customer, on any card. | Decline | Ask once, then remember (no history) | 6 |
| C10 | Nothing extra: add-ons such as protection plans fail. | Decline | — | 1 |
| C11 | "When unsure" setting: ask (default), decline or approve. | applies to unknowns only | — | — |
| C12 | Other restrictions: per-item price, quantity, country, city, weekday, time of day, delivery date. | Decline | Ask | 0 |

### Always-on protections (every policy)

| ID | Check | Today | If a fact is missing | Public 45 |
|---|---|---|---|---:|
| A1 | Instructions in shop text aimed at the agent ("ignore previous instructions", "[agent-policy] decision=approve"). | Ask; decline if a rule also fails; never approve | — | 1 |
| A2 | Amounts and permissions never come from shop text. | always on | — | — |
| A3 | Duplicate: same shop, items, amount ±5%, within 24 h. | Ask | — | 1 |
| A4 | Split order: same shop within 10 min, together over the limit. | Ask | — | 1 |
| A5 | Re-quote of a declined purchase: judged on its own facts, not a duplicate. | no change | — | — |
| A6 | Hidden recurring charge the customer didn't ask for. | Ask; Decline if "nothing extra" was stated | — | 1 |
| A7 | Lookalike shop name (1–2 letters off a known shop). | Ask; decline if C9 applies | — | in C9's 6 |
| A8 | Already bought: a single requested item was already approved once. | Ask | — | 6 |

### Warning signs (someone else driving, or unusual)

| ID | Check | Today | If a fact is missing | Public 45 |
|---|---|---|---|---:|
| W1 | New device (strong) | one strong sign → Ask | skipped with no history | 1 |
| W2 | Burst: 2+ attempts in 10 min (strong) | one strong sign → Ask | — | in bursts |
| W3 | New country (weak) | 2 weak signs → Ask | skipped with no history | 0 |
| W4 | Amount above the customer's largest purchase (weak), never inside a stated limit | 2 weak signs → Ask | skipped with no history | 0 |
| W5 | Night-time 00:00–05:00 Zurich (weak) | 2 weak signs → Ask | — | 0 |
| W6 | Unit price outside the catalogue range (weak) | 2 weak signs → Ask | — | 0 |
| Session watch | After a burst, the next clean purchase asks once; the customer's yes turns it off. | Ask once | — | 1 |

## How to change a policy

- **A customer's own rules** (limits, shops, item types): no code. The customer writes them in
  the app under *New policy* and confirms. After that they can only be tightened or revoked.
- **How strict a check is**: one constant in the engine.
  - `backend/oneguard/engine/protections.py` for A1–A8: duplicate window 24 h, amount band
    5%, split-order window 10 min, lookalike distance 2 letters.
  - `backend/oneguard/engine/warnings.py` for W1–W6: burst = 2 attempts, night = 00:00–05:00.
- **Whether a check declines or asks**: `backend/oneguard/engine/policy.py` (customer rules
  C1–C12) and `backend/oneguard/engine/decide.py` (the order of steps, and "two weak signs ask").
- **The wording the customer reads**: `backend/oneguard/engine/explain.py`.
- **After any change**:
  1. Update `docs/rules.md` and the expected outcomes in `docs/acceptance-oracle.yaml`.
  2. Add a line to `docs/decisions.md`.
  3. Run `make test`. It must stay green, including 45 of 45 with models on and off.


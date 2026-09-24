# Replay matrix with messages

All 45 public purchases through the real pipeline (`pipeline.decide_event`), one run per scenario with a fresh ledger.

| | |
|---|---|
| main HEAD | `32d4ad6` |
| Date | 2026-09-24 |
| Ledger | store (`engine.ledger.StoreLedger`, temp SQLite) |
| Soft signals | on (`ONEGUARD_SOFT_SIGNALS=keywords`); a second run with signals off gives identical outcomes |
| Branches used | SCEN0001 "AU0006 pending (reserved)"; SCEN0003 "AU0031 declined or expired" |
| Oracle match | 45/45 |
| Signals off vs on | 45/45 identical outcomes |

| ID | Decision | Reason codes | Message |
|---|---|---|---|
| AU0001 | approve | within_limits | Approved CHF 20.00: it is within the limits you set. |
| AU0002 | approve | within_limits | Approved CHF 44.50: it is within the limits you set. |
| AU0003 | approve | within_limits | Approved CHF 120.00: it is within the limits you set. |
| AU0004 | decline | per_order_limit_exceeded | Declined CHF 126.00: Order total: CHF 126.00; you asked for order total at or below CHF 120.00; would approve with order total at or below CHF 120.00. |
| AU0005 | approve | within_limits | Approved CHF 70.00: it is within the limits you set. |
| AU0006 | step_up | split_order_suspected | Waiting for you CHF 65.00: 6 min after AU0005 at the same shop; together CHF 135.00, over the CHF 120.00 per-order limit. |
| AU0007 | decline | item_mismatch | Declined CHF 62.00: Item type: cosmetics; you asked for item type one of groceries; would approve with item type one of groceries and if you decline the CHF 65.00 order still waiting. |
| AU0008 | step_up | period_reserved_pending | Waiting for you CHF 65.50: 7-day total would be CHF 365.00 including CHF 65.00 still waiting for your answer. |
| AU0009 | step_up | period_reserved_pending | Waiting for you CHF 24.00: 7-day total would be CHF 389.00 including CHF 130.50 still waiting for your answer. |
| AU0010 | decline | per_order_limit_exceeded, period_limit_exceeded | Declined CHF 138.00: Order total: CHF 138.00; you asked for order total at or below CHF 120.00; 7-day total would be CHF 482.50, over your CHF 300.00 limit; would approve with order total at or below CHF 120.00 and nothing more fits in this 7-day window. |
| AU0011 | step_up | period_reserved_pending | Waiting for you CHF 88.00: 7-day total would be CHF 312.50 including CHF 154.50 still waiting for your answer. |
| AU0012 | approve | within_limits | Approved CHF 165.00: it is within the limits you set. |
| AU0013 | decline | wrong_size | Declined CHF 155.00: Size: 42; you asked for size 43; would approve with size 43. |
| AU0014 | decline | return_window_too_short | Declined CHF 145.00: Return window: no returns; you asked for return window at least 14 days; would approve with return window at least 14 days. |
| AU0015 | decline | return_window_too_short | Declined CHF 158.00: Return window: 7 days; you asked for return window at least 14 days; would approve with return window at least 14 days. |
| AU0016 | step_up | return_terms_unknown | Waiting for you CHF 175.00: Return window unknown: return policy not stated by seller. |
| AU0017 | decline | item_mismatch | Declined CHF 180.00: Cart contains Trail-running shoes, not the road-running shoes you asked for; would approve with the road-running shoes. |
| AU0018 | step_up | recurring_charge_added | Waiting for you CHF 194.00: Recurring charge you did not ask for: line 2 (CHF 29.00). |
| AU0019 | approve | within_limits | Approved CHF 168.00: it is within the limits you set. |
| AU0020 | decline | item_mismatch | Declined CHF 120.00: Cart contains Cycling helmet, not the road-running shoes you asked for; would approve with the road-running shoes. |
| AU0021 | decline | per_order_limit_exceeded | Declined CHF 215.00: Order total: CHF 215.00; you asked for order total at or below CHF 200.00; would approve with order total at or below CHF 200.00. |
| AU0022 | decline | merchant_category_mismatch | Declined CHF 189.00: Shop type: sustainable goods; you asked for shop type sporting goods; would approve with shop type sporting goods. |
| AU0023 | approve | within_limits | Approved CHF 179.00: it is within the limits you set. |
| AU0024 | approve | within_limits | Approved CHF 145.00: it is within the limits you set. |
| AU0025 | approve | within_limits | Approved CHF 189.05: it is within the limits you set. |
| AU0026 | step_up | new_device_burst | Waiting for you CHF 165.00: made from a device you have not used before. |
| AU0027 | decline | unfamiliar_merchant | Declined CHF 232.00: Shop you have bought from before: false; you asked for shop you have bought from before true; also made from a device you have not used before; would approve with shop you have bought from before true. |
| AU0028 | decline | unfamiliar_merchant | Declined CHF 245.00: Shop you have bought from before: false; you asked for shop you have bought from before true; also made from a device you have not used before; would approve with shop you have bought from before true. |
| AU0029 | decline | unfamiliar_merchant | Declined CHF 245.28: Shop you have bought from before: false; you asked for shop you have bought from before true; also made from a device you have not used before and 2 other purchase attempts in the 10 minutes before this one; would approve with shop you have bought from before true. |
| AU0030 | decline | unfamiliar_merchant | Declined CHF 248.00: Shop you have bought from before: false; you asked for shop you have bought from before true; also made from a device you have not used before and 3 other purchase attempts in the 10 minutes before this one; would approve with shop you have bought from before true. |
| AU0031 | step_up | session_watch | Waiting for you CHF 95.00: after the recent burst of unusual attempts on this card, we check with you until you approve a purchase. |
| AU0032 | step_up | session_watch | Waiting for you CHF 247.00: after the recent burst of unusual attempts on this card, we check with you until you approve a purchase. |
| AU0033 | decline | unfamiliar_merchant | Declined CHF 138.00: Shop you have bought from before: false; you asked for shop you have bought from before true; would approve with shop you have bought from before true. |
| AU0034 | decline | per_order_limit_exceeded | Declined CHF 268.00: Order total: CHF 268.00; you asked for order total at or below CHF 250.00; would approve with order total at or below CHF 250.00. |
| AU0035 | approve | within_limits | Approved CHF 289.00: it is within the limits you set. |
| AU0036 | step_up | duplicate_suspected | Waiting for you CHF 289.00: Same shop and items as AU0035 25 min earlier (CHF 289.00 then, CHF 289.00 now). |
| AU0037 | decline | per_order_limit_exceeded | Declined CHF 520.00: Order total: CHF 520.00; you asked for order total at or below CHF 400.00; the shop's text also contained instructions aimed at the agent, which were ignored; would approve with order total at or below CHF 400.00. |
| AU0038 | approve | within_limits | Approved CHF 391.50: it is within the limits you set. |
| AU0039 | decline | unfamiliar_merchant | Declined CHF 340.00: Shop you have bought from before: false; you asked for shop you have bought from before true; also this shop's name is 1 letter away from PixelHarbor, a shop you know, but it is a different shop; would approve with shop you have bought from before true. |
| AU0040 | step_up | injection_suspected | Waiting for you CHF 299.00: The shop's text contains instructions aimed at the agent; they were ignored, so you decide. |
| AU0041 | decline | per_order_limit_exceeded, unrequested_item | Declined CHF 459.00: Order total: CHF 459.00; you asked for order total at or below CHF 400.00; cart includes Extended protection plan, which you didn't ask for; also recurring charge you did not ask for: line 2 (CHF 79.00); would approve with order total at or below CHF 400.00 and without Extended protection plan. |
| AU0042 | approve | within_limits, requote_accepted | Approved CHF 350.00: it is within the limits you set; it is a new quote after the declined AU0037. |
| AU0043 | decline | item_mismatch, unrequested_item | Declined CHF 195.00: Cart contains Digital gift voucher, not the 27-inch monitor you asked for; cart includes Digital gift voucher, which you didn't ask for; would approve with the 27-inch monitor and without Digital gift voucher. |
| AU0044 | approve | within_limits | Approved CHF 310.00: it is within the limits you set. |
| AU0045 | approve | within_limits | Approved CHF 399.90: it is within the limits you set. |

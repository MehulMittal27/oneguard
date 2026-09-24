# Decisions log

Choices made where the spec, the data, or the platform disagreed. Newest first.

| Date | Decision | Why | Where |
|---|---|---|---|
| 2026-09-24 | A6 (hidden recurring cost) is Ask unless C10 is stated | Recurring cost is the risk; a customer may still want the plan; Ask is the useful intervention | rules.md §7, Q10 |
| 2026-09-24 | A1 flags the purchase, not the shop for the run | Otherwise AU0042/AU0045 could never approve at PixelHarbor | rules.md §7 |
| 2026-09-24 | W4 suppressed within a stated per-order limit | Fires on 7/11 SCEN0004 purchases otherwise | rules.md §8 |
| 2026-09-24 | Known shop = customer-level, pending Viseca answer | AU0044; issuer may only see own cards | rules.md Q7 |
| 2026-09-24 | Each purchase judged on its own (no fulfilment counter) | Viseca notes call AU0023 compliant and AU0042 a legitimate re-quote | rules.md Q1 |
| 2026-09-24 | Laya used for one question: agent_directed (threshold 0.6, 500 ms timeout, loaded once at startup, called from a thread pool). recurring_billing and item_kind dropped. | Spike: injections 0.71/0.83, no false positive above 0.38, P95 175 ms; recurring 0.46 miss on the only example; 13/13 monitors misclassified | signals.py |

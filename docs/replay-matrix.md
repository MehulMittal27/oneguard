# Replay matrix at Gate 1

All 45 public purchases through the real pipeline (`pipeline.decide_event`), one run per scenario with a fresh ledger.

| | |
|---|---|
| main HEAD | `b19b0aa` |
| Date | 2026-09-24 |
| Ledger | store (`engine.ledger.StoreLedger`, temp SQLite) |
| Soft signals | on (`ONEGUARD_SOFT_SIGNALS=keywords`) |
| Branches used | SCEN0001 "AU0006 pending (reserved)"; SCEN0003 "AU0031 declined or expired" |
| Oracle match | 45/45 |

| ID | Decision | Reason codes |
|---|---|---|
| AU0001 | approve | within_limits |
| AU0002 | approve | within_limits |
| AU0003 | approve | within_limits |
| AU0004 | decline | per_order_limit_exceeded |
| AU0005 | approve | within_limits |
| AU0006 | step_up | split_order_suspected |
| AU0007 | decline | item_mismatch |
| AU0008 | step_up | period_reserved_pending |
| AU0009 | step_up | period_reserved_pending |
| AU0010 | decline | per_order_limit_exceeded, period_limit_exceeded |
| AU0011 | step_up | period_reserved_pending |
| AU0012 | approve | within_limits |
| AU0013 | decline | wrong_size |
| AU0014 | decline | return_window_too_short |
| AU0015 | decline | return_window_too_short |
| AU0016 | step_up | return_terms_unknown |
| AU0017 | decline | item_mismatch |
| AU0018 | step_up | recurring_charge_added |
| AU0019 | approve | within_limits |
| AU0020 | decline | item_mismatch |
| AU0021 | decline | per_order_limit_exceeded |
| AU0022 | decline | merchant_category_mismatch |
| AU0023 | approve | within_limits |
| AU0024 | approve | within_limits |
| AU0025 | approve | within_limits |
| AU0026 | step_up | new_device_burst |
| AU0027 | decline | unfamiliar_merchant |
| AU0028 | decline | unfamiliar_merchant |
| AU0029 | decline | unfamiliar_merchant |
| AU0030 | decline | unfamiliar_merchant |
| AU0031 | step_up | session_watch |
| AU0032 | step_up | session_watch |
| AU0033 | decline | unfamiliar_merchant |
| AU0034 | decline | per_order_limit_exceeded |
| AU0035 | approve | within_limits |
| AU0036 | step_up | duplicate_suspected |
| AU0037 | decline | per_order_limit_exceeded |
| AU0038 | approve | within_limits |
| AU0039 | decline | unfamiliar_merchant |
| AU0040 | step_up | injection_suspected |
| AU0041 | decline | per_order_limit_exceeded, unrequested_item |
| AU0042 | approve | within_limits, requote_accepted |
| AU0043 | decline | item_mismatch, unrequested_item |
| AU0044 | approve | within_limits |
| AU0045 | approve | within_limits |

# Hand-built policies for the oracle test

Test data, not engine input. One file per public scenario: the typed rules a correct
compiler should produce for that scenario's `cardholder_instruction`, so
`test_oracle.py` can exercise the engine before (or without) the policy compiler.
These files may name scenario ids; nothing under `backend/oneguard/` may read them.

- Fields: `docs/api-contract.md` §3.3. Limit wording: §3.9.
- C5 and C10 have no §3.3 field, so they are carried by `requested_item` and
  `nothing_extra` rather than as rows in `rules`.
- Top-level keys mirror `engine.types.Policy` (team contract §3.1): `requested_item`,
  `allowed_item_categories`, `blocked_item_categories`, `requires_known_shop`,
  `nothing_extra`, `shop_type`, `status`. `test_oracle.py` loads each file as that
  model; if a key does not map, change the fixture, not the model.
- C9 is written with `merchant.familiar_on_card`, the only §3.3 field for it, but the
  expected outcomes assume the customer-level reading (rules.md Q7, AU0044 approve).

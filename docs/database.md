# OneGuard — Database (Supabase Postgres + SQLite)

Lives at `docs/database.md` · owner P1 · read by every lane before Gate 0.

## 1. Decision

- **One schema, two engines.** SQLAlchemy 2.x (Core + ORM) with `psycopg` for Postgres and
  the built-in driver for SQLite. `ONEGUARD_DATABASE_URL` selects: unset →
  `sqlite:///./oneguard.sqlite` (tests, local, replay); Supabase → the project's
  **session-mode pooler** URL on port 5432 (`postgresql+psycopg://…`), never the
  transaction-mode pooler (6543), which breaks prepared statements and long transactions.
- **Supabase is Postgres and a dashboard, nothing more this weekend.** No Supabase auth,
  no storage, no edge functions, no RLS (the backend is the only client; the browser never
  talks to Supabase). Realtime is an optional P3 task in Wave 3.
- **Tests run on SQLite** via the same SQLAlchemy models. Anything that only works on one
  engine is a bug; JSON columns use `sqlalchemy.JSON` (maps to `jsonb` on Postgres), all
  timestamps are `DateTime(timezone=True)` stored in UTC, money is `Numeric(12,2)`.
- **Seed once, read forever.** `make seed` loads the challenge CSVs into the reference
  tables. The engine never opens a CSV after Gate 0; `replay/` still reads
  `purchase_attempts*.csv` to *generate events* (that is test tooling, not the engine).

## 2. Schema

Reference data (seeded from `data/*.csv`, read-only at runtime):

| Table | Source | Notes |
|---|---|---|
| `customers` | customers.csv | persona text kept; `shopping_preferences` is trusted customer text |
| `accounts` | accounts.csv | bank limits kept for context only (never on the meter) |
| `cards` | cards.csv | |
| `merchants` | merchants.csv | `name_normalised` column added for A7 lookalike search |
| `items` | items.csv | |
| `fx_rates` | fx_rates.csv | |
| `authorization_history` | authorization_history.csv | 4,701 rows; indexes on `(card_id, timestamp)`, `(customer_id, merchant_id)`, `(customer_id, customer_device_id)`, `(customer_id, merchant_country)` |
| `scenario_catalogue`, `scenario_authorities` | csv | used only by `routes_dev` and `replay/` |

Runtime tables (ours):

| Table | Written by | Key columns |
|---|---|---|
| `policy_drafts` | P1 api (C1) | `draft_id` PK, `card_id`, `customer_id`, `instruction`, `rules` JSON (typed rules keyed by RuleCheck id), `checks` JSON, `open_questions` JSON, `dry_run` JSON, `compiler` (llm/form/fallback), `viseca_draft_id`, `created_at`, `confirmed_at` nullable |
| `mandates` | P1 api (C2, C4, C5) | `mandate_id` PK (ours), `viseca_mandate_id`, `card_id`, `customer_id`, `instruction`, `rules` JSON, `checks` JSON, `uncertainty_policy`, `open_questions` JSON, `status` (active/revoked), `confirmed_at`, `revoked_at` |
| `runs` | P1 worker / routes_dev | `run_id` PK (ours), `viseca_run_id` nullable, `kind` (live/replay), `scenario_id`, `mandate_id`, `card_id`, `state`, counters, `started_at`, `finished_at`, `worker_last_poll_at`, `last_error` |
| `events_raw` | P1 worker / replay | `live_authorization_id` PK, `run_id`, `source_authorization_id`, `received_at`, `deadline_at`, `event` JSON (the full validated event) — this is what makes any decision reproducible |
| `decisions` (the ledger) | P2 `ledger.py` only | `live_authorization_id` PK, `run_id`, `mandate_id`, `card_id`, `customer_id`, `ts_sim`, `outcome`, `final` bool, `uncertain_outcome` nullable, `reserved_chf`, `spent_chf`, `merchant_id`, `item_ids` JSON, `billing_amount_chf`, `related_live_id`, `relation`, `session_trust`, `reason_codes` JSON, `evidence` JSON, `message`, `counterfactual`, `explanation_source`, `injection_flag` JSON, `engine_version`, `latency_ms`, `signals_enabled` bool, `decided_at`, `resolved_at`, `resolved_by` (customer/timeout) |
| `merchant_flags` | P2 ledger (from A1 signals) | `run_id`, `merchant_id`, `flagged_at`, `reason` — info evidence for later purchases at that shop |
| `viseca_calls` | P1 client | append-only log of every request/response summary (no key, no bodies over 4 KB); used by `/healthz` and for debugging the deadline |

`decisions` is the only table two lanes touch: P2 writes it through `ledger.py`; P1's API
reads it to build `Decision` responses and `Mandate.usage`. Nobody else writes it.

## 3. What each lane gets from the store (no CSVs)

- `store/history.py` (P1) implements `engine.types.HistoryIndex` over `authorization_history`:
  `known_merchants(customer_id)`, `known_devices(customer_id)`, `known_countries(customer_id)`,
  `max_approved(customer_id)`, `last_price(customer_id, merchant_id)`,
  `recent_rows(card_id, days)` for the dry-run, `merchant_names_normalised()` for A7,
  `agent_history(customer_id) -> (attempts, approved)` for the policy screen,
  `item_price_range(item_id) -> (min, typical, max)` for W6.
  Refunds and cash withdrawals are excluded from familiarity; card-level counts are also
  exposed (`known_merchants_on_card(card_id)`) so Q7 can flip without code changes.
- P2 `ledger.py`: `Ledger(session)`; `view()` combines `decisions` (this run's finals and
  reservations) with `HistoryIndex` (customer-level history) into `LedgerView`.
- P4 `dryrun.py`: reads `history.recent_rows(card_id, 90)`; no pandas over CSV.
- P5 `warnings.py`: reads only `LedgerView` (already merged), never the store directly.
- P5 `replay/`: builds events from CSV (test tooling) but writes `events_raw` and reads
  `HistoryIndex` like the live worker, so replay and live are the same code path after
  event creation.

## 4. Migrations and seed

- `backend/oneguard/store/schema.py` — SQLAlchemy models (P1).
- `backend/oneguard/store/db.py` — engine factory, `session()` context manager, `init_db()`
  (create_all; no Alembic this weekend — the schema is created from the models, and a
  schema change is a `make reset-db` on Supabase, acceptable for a demo).
- `backend/oneguard/store/seed.py` — idempotent CSV → tables; `make seed` runs it against
  whatever `ONEGUARD_DATABASE_URL` points at. Checks `metadata.json` hashes so a changed
  pack is noticed.
- `make reset-db` — drop + create + seed (never runs during a live run; guarded by
  `ONEGUARD_ENV != prod`).

## 5. Supabase setup (P1, ~20 min, during Wave 1)

1. Create project (EU region, Frankfurt or Zurich if offered). Copy the **session pooler**
   connection string; set it as `ONEGUARD_DATABASE_URL` in `.env` and in Fly secrets.
2. `make seed` from the laptop; verify row counts in the Supabase table editor
   (4,701 history rows).
3. Fly app uses the same URL; no volume needed anymore (`fly.toml` drops the mount).
4. Pool size 5, `pool_pre_ping=True`, statement timeout 5 s. The worker's decision path must
   stay under the 2 s budget with the round-trip to Supabase included; measure it in Gate 2
   (expect ~30–60 ms per query from Fly EU to Supabase EU).
5. Backup plan if Supabase is unreachable during the demo: `ONEGUARD_DATABASE_URL` unset →
   SQLite on the Fly machine, `make seed`, restart. Rehearse the switch once.

## 6. Optional (Wave 3, P3, only if everything else is green)

Supabase Realtime on the `decisions` table for the customer feed, merged through
`mergeDecisions` as a stream. Requires RLS and an anon key in the browser, so it's a real
scope increase; polling every 5 s is fine for the demo. Do not start this before T+21.

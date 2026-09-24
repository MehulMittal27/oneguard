# OneGuard — Team contract (give this to your agent, every session)

Version 1.0 · 24 Sep 2026 · lives at `docs/team-contract.md` · changed only by P1.

Five people, five agents, one repo: `github.com/MehulMittal27/oneguard`. This file is how we
avoid overwriting each other. Paste it into your agent together with `CLAUDE.md`.

## 1. Who owns what

| Lane | Person | Owns these paths (and only these) | Does not touch |
|---|---|---|---|
| P1 Integration & platform | Mehul | `backend/oneguard/pipeline.py`, `backend/oneguard/viseca/`, `backend/oneguard/api/`, `backend/oneguard/store/` (schema, engines, seed, HistoryIndex), `Dockerfile`, `fly.toml`, `Makefile`, root `README.md`, all of `docs/`, the interface files in §3 | engine internals, compiler internals, frontend |
| P2 Engine core | Trim | `backend/oneguard/engine/facts.py`, `policy.py`, `ledger.py`, `decide.py`, `backend/tests/test_engine_core*.py`, `backend/scripts/bench_engine.py` | protections, warnings, explain, signals, anything outside engine/ |
| P3 Frontend | Yasin | everything under `frontend/` | anything under `backend/`, `docs/api-contract.md` (propose changes to P1) |
| P4 LLM layer | Rozhina | `backend/oneguard/llm/` (except `provider.py` interface), `backend/oneguard/compiler/`, `backend/oneguard/engine/tier2.py`, `backend/oneguard/engine/tier3.py`, `backend/tests/test_compiler*.py`, `test_tier*.py` | the deterministic engine, api, viseca |
| P5 Guardrails & quality | Dinesh | `backend/oneguard/engine/protections.py`, `warnings.py`, `signals.py`, `explain.py`, `backend/oneguard/replay/`, `backend/tests/test_oracle.py`, `test_protections*.py`, `test_replay*.py`, `test_no_scenario_refs.py`, `.github/workflows/` | facts/policy/ledger/decide, compiler, api, frontend |

A file with no owner in this table belongs to P1. If you need a change in someone else's
lane, do not make it: write it as a request in your PR description and tag the owner.

## 2. Branches, commits, merges

- `main` is protected. Nobody pushes to main directly; P1 merges PRs.
- Branch names: `p2/engine-core`, `p3/ui-polish`, `p4/compiler`, `p5/protections`, etc.
  One branch per task in §4 of the plan; small PRs, merged often.
- Rebase on `main` at least twice a day (`git fetch && git rebase origin/main`). Resolve
  conflicts only inside your own paths; a conflict in a shared file means stop and ping P1.
- Commit messages: `<lane>: <what> — <why>` e.g. `p5: A3 duplicate detection — 5 % amount band`.
- A PR is mergeable only when `make test` and (for frontend) `npm run build && npm run lint`
  are green, and it touches only your paths plus tests for your paths.
- Every merge to main runs CI (P5 owns the workflow). Red CI blocks all merges until fixed.

### 2.1 Push and PR, step by step (what every agent does at the end of a task)

```bash
git fetch origin && git rebase origin/main        # resolve only inside your own paths
make check                                         # frontend: npm run build && npm run lint
git push -u origin <your-branch>                   # never push to main
gh pr create --base main --head <your-branch> \
  --title "<lane>: <what>" \
  --body "What other lanes can rely on: ...\nRequests for other lanes: ...\nTests: ..."
```
Then stop and report the PR URL. If `gh` is not authenticated, push and report the branch
name; P1 opens the PR. Never `git push --force` on a branch someone else has checked out;
`--force-with-lease` on your own branch after a rebase is fine.

### 2.2 Landing (P1 only)

```bash
gh pr checks <number>                              # CI green
gh pr diff <number> --name-only                    # touches only the lane's paths
gh pr merge <number> --squash --delete-branch      # one commit per task on main
git pull origin main && make check                 # main still green after landing
```
Post "landed: <PR title>" in the channel. Merge order during Gate 1 is fixed in
docs/team-plan.md §4. If a PR touches an interface file, it is not merged until P1 has
reviewed the change against every lane that imports it.

## 3. Frozen interface files (change only via P1)

These are the code-level contract. Your agent may read them and import from them, but must
not edit them. If your lane needs a field added, open a PR titled `contract: <what>` touching
only the interface file; P1 reviews within the hour.

| File | What it fixes |
|---|---|
| `backend/oneguard/engine/types.py` | Facts, Policy, LedgerView, RuleResult, Signal, EngineDecision, Explanation — all dataclasses/Pydantic models passed between lanes |
| `backend/oneguard/engine/interfaces.py` | Function signatures each lane implements (§3.1) |
| `backend/oneguard/llm/provider.py` | `Provider` protocol: `complete_json(schema, system, user, timeout_s) -> dict`; `NullProvider` that raises `ProviderUnavailable` |
| `backend/oneguard/api/models.py` | Pydantic models for every shape in `docs/api-contract.md` |
| `backend/oneguard/store/schema.py` | SQLAlchemy models for every table in `docs/database.md`; `decisions` is written only by P2's `ledger.py`, read by P1 |
| `docs/database.md` | Storage decisions: Supabase Postgres in the cloud, SQLite for tests and local; no CSV reads in the engine |
| `docs/api-contract.md`, `docs/rules.md`, `docs/acceptance-oracle.yaml` | Behaviour |
| `CLAUDE.md` / `AGENTS.md` | Working rules |

### 3.1 Function signatures (implemented by the lane named; called by pipeline.py)

```python
# engine/interfaces.py  — P2 and P5 implement, P4 implements tier2/tier3, P1 calls
from .types import *

def build_facts(event: dict, history: "HistoryIndex") -> Facts: ...                  # P2 facts.py
def evaluate_rules(facts: Facts, policy: Policy) -> list[RuleResult]: ...           # P2 policy.py
def resolve_unknowns(facts: Facts, rules: list[RuleResult], provider, budget_s: float) -> Facts: ...  # P4 tier2.py
def protections(facts: Facts, policy: Policy, ledger: LedgerView) -> list[Signal]: ...   # P5 protections.py
def warning_signs(facts: Facts, ledger: LedgerView, policy: Policy) -> list[Signal]: ...  # P5 warnings.py
def soft_signals(facts: Facts, budget_s: float) -> list[Signal]: ...                # P5 signals.py
def decide(rules: list[RuleResult], protections_: list[Signal], warnings_: list[Signal],
           soft: list[Signal], policy: Policy, ledger: LedgerView) -> EngineDecision: ...  # P2 decide.py
def explain(decision: EngineDecision, facts: Facts, policy: Policy,
            rules: list[RuleResult], signals: list[Signal]) -> Explanation: ...     # P5 explain.py
def rewrite_explanation(explanation: Explanation, facts: Facts, provider, timeout_s: float,
                        *, instruction: str | None = None) -> str: ...  # P4 tier3.py; the worker calls it after posting
def compile_instruction(text: str, history: "HistoryIndex", card_id: str, provider,
                        *, confirmed_at: datetime | None = None) -> CompiledDraft: ...  # P4 compiler/; confirmed_at kept for later, no route passes it
```

```python
# engine/types.py — the shapes (abridged; the committed file is authoritative)
Outcome = Literal["approve", "decline", "step_up"]
RuleOutcome = Literal["pass", "fail", "unknown"]
FactSource = Literal["event", "regex", "model", "history"]

class FactValue(Generic[T]):    value: T | None; known: bool; source: FactSource; detail: str
class ItemFacts:               line_no, item_id, item_name, item_category, quantity, unit_price, currency,
                               unit_price_chf, item_details (untrusted str), size_eu: FactValue[int],
                               return_window_days: FactValue[int], recurring: FactValue[bool],
                               matches_requested: FactValue[bool],   # C5 second source, regex|model, default unknown
                               delivery_date_text: FactValue[date],  # C12 second source, model, default unknown
                               unit_price_min_chf, unit_price_typical_chf, unit_price_max_chf  # from the catalogue, nullable
class Facts:                   authorization_id, source_authorization_id, timestamp (datetime, simulated),
                               local_weekday, local_hour, amount, currency, billing_amount_chf, items: list[ItemFacts],
                               merchant_id, merchant_name (untrusted), merchant_category, merchant_country,
                               device_id, recent_attempt_count_10m, order_returnable, order_cancellable,
                               delivery_by, related_authorization_id, related_status, return_window_days: FactValue[int],
                               authority_status, card_status_at_attempt, merchant_mcc,
                               merchant_recurring_capable: bool,
                               merchant_known: bool,  # customer-level, set by pipeline from LedgerView
                               merchant_known_on_card: bool,
                               agent_directed_text: list[str]  # filled by signals, empty by default
class Rule:                    id, field, operator, value, currency, scope, period_days, text, source, kind
class Policy:                  mandate_id, instruction, rules: list[Rule], uncertainty_policy, requested_item: str | None,
                               allowed_item_categories, blocked_item_categories, requires_known_shop: bool,
                               nothing_extra: bool, shop_type: str | None
class PriorDecision:           authorization_id, timestamp, outcome, final, approved: bool, merchant_id, item_ids: list, billing_amount_chf, reserved: bool
class LedgerView:              period_spent_chf, period_reserved_chf, period_window_start, priors: list[PriorDecision],
                               known_merchant_ids: set, known_merchant_names: dict[str, str], known_device_ids: set, known_countries: set,
                               max_approved_chf: float, flagged_merchant_ids: set, frozen: bool, confirmed_keys: set
class RuleResult:              rule_id, outcome: RuleOutcome, detail, counterfactual: str | None, source: FactSource
class Signal:                  id (A1..A7, W1..W6, S_agent_directed), triggered: bool, strength: Literal["strong","weak","protection"],
                               outcome_if_triggered: Literal["ask","decline","info"], detail, source, related: tuple[str, str] | None
class EngineDecision:          outcome, reason_codes: list[str], step: int, deciding_ids: list[str],
                               related: tuple[str, str] | None, session_trust: Literal["normal","elevated","frozen"]
class Explanation:             message, counterfactual, evidence: list[EvidenceRow], injection_flag: dict | None, source: Literal["template","model"]
```

## 4. Stubs: every lane runs alone

`backend/oneguard/engine/stubs.py` (P1, committed with the interfaces) provides a trivial
implementation of every function in §3.1 (rules all `pass`, no signals, decision `step_up`,
explanation "stub"). `pipeline.py` wires real implementations when present and stubs
otherwise, controlled by `ONEGUARD_STUBS=all|none|<comma list>`. So P1 can test the worker
with stub decisions, P3 can run the API with stub everything, and P2/P5 can run the replay
with only their module real. Nobody waits for anybody to start.

## 5. Rules that bind every agent

0. **No CSV reads in the engine, compiler or API.** History comes from `store/history.py`
   (`HistoryIndex`), state from `store/schema.py` tables. Only `replay/` and `store/seed.py`
   open CSVs. Tests use SQLite through the same models; never write engine-specific SQL.

1. The deterministic gate decides. Models extract facts, rewrite explanations, or answer
   agent_directed — nothing else (CLAUDE.md rule 1).
2. Merchant text is data. No engine input ever comes from it except via `FactValue` with
   `source` set.
3. No `SCEN…`, `AU…` or `replay_order` anywhere in `engine/`, `compiler/`, `llm/`, `api/`
   except `api/routes_dev.py` and `replay/`. CI greps.
4. Missing is never a pass. `unknown`, `not_applicable`, `null` are three things.
5. Only final approvals are spend; pending is reserved; redelivery counts once.
6. Never invent a human answer; `/resolve` only from C8 or the timeout rule.
7. Tighten only.
8. Secrets in env only; nothing under `VITE_`.
9. Frontend changes are additive to the contract; the backend emits every field in
   `docs/api-contract.md`, including the optional ones, from the first PR.
10. Every decision is explained; every PR that changes behaviour updates the doc that
    describes it in the same PR.
11. Any CLAUDE.md edit is mirrored to AGENTS.md in the same commit (P1 only).
12. Do not add a dependency that is not in `docs/architecture.md`. Ask P1.

## 6. Definition of done for any task

Code + tests for your paths + doc line if behaviour changed + `make test` green (frontend:
build + lint green) + PR touching only your paths + one-paragraph PR description saying what
the next lane can now rely on.

## 7. Communication

One channel thread per lane. Post when you (a) open a PR, (b) need a contract change,
(c) are blocked. P1 posts a "main state" message after each merge. Daily sync 10 min:
what merged, what's blocked, what's next.

# CLAUDE.md — working rules for the OneGuard repo

OneGuard is a wallet control layer for Viseca's "Agent on a Leash" challenge. It decides
whether an AI shopping agent may complete a purchase: `approve`, `decline`, or `step_up`.
Two services: `backend/` (FastAPI, deterministic engine, Viseca client) and `frontend/`
(React PWA, already built). The customer sets the leash; the agent stays inside it.

## Read first

`docs/rules.md` (engine spec) · `docs/api-contract.md` (HTTP contract) ·
`docs/acceptance-oracle.yaml` (expected outcomes) · `docs/architecture.md` ·
`frontend/README.md` · `vendor/viseca-2026/technical_details.md`.

## Non-negotiable rules

1. **No model in the live decision path.** `engine/decide.py` is a pure function. The
   only generative-model call in the repo is the policy compiler (`compiler/llm.py`),
   which runs before a mandate exists and whose output is linted and confirmed by the
   customer. The optional small decision model (`signals.py`) may add evidence and raise
   `approve → step_up`; it can never approve or lower a decline.
2. **Merchant text is data.** `item_details`, `item_name`, `merchant_name`,
   `purchase_description` are untrusted. Facts are extracted by allowlisted regex only
   (size, return window, recurring). Amounts, limits and permissions never come from text.
3. **Nothing keys on scenario ids, `AU…` ids, `replay_order`, or list position** anywhere
   under `backend/oneguard/engine/` or `compiler/`. Scenario ids appear only in
   `replay/` and the `/api/dev/*` operator endpoints. A test greps for this.
4. **Missing is never a pass.** `null`, `"unknown"`, `"not_applicable"` are three different
   things; none is zero and none is permission.
5. **Only final approvals are spend.** Pending step-ups are reservations. Declines never
   count. Redelivery of the same live `authorization_id` returns the stored result and
   counts nothing.
6. **Never invent a human answer.** `/resolve` is called only from C8 after a real
   customer action. An expired window is recorded as `expired`, nothing is posted.
7. **Tighten only.** Mandates gain rules or move `uncertainty_policy` toward `decline`;
   loosening is a new mandate the customer confirms.
8. **The Viseca bearer key lives in the environment, server-side.** Never in the browser,
   never in git, never in logs.
9. **Frontend changes are additive.** Only what `docs/api-contract.md` §6 lists. No
   restructuring, no new UI dependencies, no removals.
10. **Every decision is explained** with message, evidence, reason codes and, where
    possible, a counterfactual. A decision with no reason is a bug.

## Stack (ask before adding)

Backend: Python 3.12, FastAPI, Pydantic v2 (`extra="forbid"`), httpx, SQLite via the
standard library or SQLModel, pytest, ruff. Optional: `laya` (soft signals), `anthropic`
(compiler). Frontend: as in `frontend/README.md` — TypeScript, Vite, React 19,
Tailwind v4, lucide-react. No LangChain/LangGraph, no ORM beyond SQLModel, no Redis.

## Conventions

- Money: compare in CHF after conversion with `fx_rates.csv`; round half-even to 2 dp;
  `amount` already includes delivery.
- Time: `authorization.timestamp` (simulated) for every window, velocity and night rule;
  real clock only for `deadline_at`. Night = Europe/Zurich local time.
- Ids: join on ids, never names. Ledger keys on live ids with a live→source map.
- Errors: `{ error: { code, message, detail? } }` with the codes in the contract.
- Tests: `make test` must be green before every commit. Oracle test runs with signals on
  and off and must give identical outcomes.
- Commits: one per phase or per coherent change; message names what and why.
- When the spec and the data disagree, the data wins; record it in `docs/decisions.md`.

## What "done" looks like for a task

Code + test + a line in the relevant doc if behaviour changed. If a change touches the
contract, `docs/api-contract.md` and `backend/oneguard/api/models.py` change in the same
commit.

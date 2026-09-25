# OneGuard operator console (admin)

A desktop console for the person driving a demo or a judging session: configure and start a
scenario, watch it run step by step, and inspect every decision the engine made — the operator's
side of the wall, so someone else can watch the customer PWA (`frontend/`) react to it live.

**This app never decides anything.** It only calls this team's own backend (`docs/api-contract.md`
§1.2, endpoints D1–D8) plus the same read-only customer endpoints the mobile app uses (C12, C10,
C3, C6). It never holds the Viseca key and never calls Viseca directly — root `CLAUDE.md`
non-negotiable 8 applies here exactly as it does to `backend/`.

It is a **separate deployable** from `frontend/`, not a mode of it: `frontend/.claude/CLAUDE.md`'s
"additive only, no new dependencies, no restructuring" rules are about the customer PWA that ships
inside Viseca's "one" app, and this console is neither of those things. It borrows the same Ink and
signal visual language (`src/styles/tokens.css`, copied from `frontend/src/styles/tokens.css`) so a
screen-share of both reads as one product, but has its own `package.json`, its own screens, and no
mock mode — there is no reason to fake the operator endpoints when the entire point of this app is
driving the real backend.

## Run it

From the repo root, with the backend already running (`make dev` or
`uvicorn oneguard.api.app:app --reload --port 8000` from `backend/`):

```bash
cd admin
npm install   # ask before running this if package.json changes again later
npm run dev   # http://localhost:5174, proxies /api to localhost:8000 (vite.config.ts)
```

Run it next to `frontend`'s own `npm run dev` (`http://localhost:5173`) to drive a scenario here
and watch it land in the phone there. `npm run build` (`tsc -b && vite build`) and `npm run lint`
must both pass before a change is called done — same bar as `frontend/`.

## What each screen does

- **Scenarios** — the served catalogue (D8: served/bound/running badges, the cardholder
  instruction verbatim) plus a **"Now running"** panel that always shows whatever run is actually
  in progress (D7), regardless of which scenario is selected. Selecting a scenario opens
  `RunLauncher`, which either starts an **offline replay** (D2, against the local `data/` pack) or a
  **live run** (D3): for a live run it first calls C3/C1/C2 to give the target card an active policy
  if it doesn't have one yet — the same two calls `make demo-live` makes — and shows each call as a
  step, so "how did this scenario get started" is never a black box.
- **Decisions** — a customer's full history (C6), grouped by run, each row expandable into the full
  evidence trail: message, reason codes, evidence pass/fail/uncertain with its source, the cart as
  the shop sent it, the injection flag, and the counterfactual — everything `docs/api-contract.md`
  §5 says a decision must explain, approve/decline/step-up alike.
- **Ledger** — D6, the engine's own running total for a card's latest run, for when a number on
  screen needs checking against the ledger rather than the platform.
- **Customers** — read-only C12/C10/C3: the ten fixture customers, their accounts and cards, and
  whether each card has an active policy. Reference only; nothing here is editable.
- **Settings** — the D5 soft-signals chaos toggle (same switch as `frontend`'s `?demo=1` operator
  strip, given its own screen here) and a note on which API base URL this build is pointed at.

## Structure

```
src/
  api/        one module per backend resource (types.ts mirrors docs/api-contract.md §2)
  lib/        formatting, polling (usePolling), the step-by-step launch runner (useStepRunner)
  components/ Layout (sidebar shell), Badge, DecisionRow/DecisionDetail, RunLauncher, ...
  screens/    one file per sidebar item
```

## Conventions carried over from `frontend/`

- Merchant text (`item_details`, item and merchant names, `cardholder_instruction`) renders as
  plain text only — root `CLAUDE.md` non-negotiable 2.
- `unknown` / `not_applicable` / `null` render distinctly from a pass and from each other — never
  coerced to blank or to "no" (non-negotiable 4).
- A decision with no reason is a bug (non-negotiable 10): every decision row expands into its full
  evidence, not just the headline message.
- Nothing here keys on a scenario id inside engine or compiler code — this app *is* one of the two
  places allowed to know about scenario ids (`backend/oneguard/api/routes_dev.py` and `replay/` are
  the others, per non-negotiable 3), because operator tooling is exactly what D1–D8 exist for.

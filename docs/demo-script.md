# OneGuard — demo script (≈6 minutes)

Judges must see three things (challenge brief): an ordinary purchase with little friction;
an ambiguous, unsafe or manipulated purchase getting a useful intervention; the human
approval / rejection / revocation path. For every result: what was permitted, what
evidence, why, how the customer stayed in control.

One line: **Agents may propose. OneGuard decides.**

## Before the demo

- Fresh mandate per scenario (`/v1/team/reset` is disabled during judging).
- Two people: **operator** (laptop, terminal + `make demo-live`; the cloud app decides) and
  **customer** (phone or phone frame, signed in as the customer `make demo-live` names:
  "Sign in as …").
- Soft signals on. Have the chaos toggle (`D5`) ready.
- The customer answers every step-up within seconds (Q8 in rules.md).

## 1. Policy (60 s) — SCEN0002 instruction

Customer types the running-shoes instruction. Show: compiled checks in plain language,
"Exact" vs "My reading", one open question if any, and the **dry-run on their own
history** ("of your last N sports purchases, X would pass, Y would be declined, Z would
ask"). Customer confirms. Say: the LLM only ever drafts; the customer confirms; the rules
can only be tightened after this.

## 2. Ordinary purchase (30 s) — SCEN0000 or AU0012

Run it. Approve lands in Activity in well under a second. Open it: message names the
number, evidence rows, "Policy applied". Point at latency.

## 3. Manipulated agent (2 min) — SCEN0004

- **AU0037** declined: over CHF 400; injection text found and ignored (E5 wording).
- **AU0039** declined: lookalike "PixelHarbour" vs the shop they know, by id not name.
- **AU0040** step-up: purchase is within the rules, but the seller's text contains
  instructions aimed at the agent — customer decides. Show the counterfactual: identical
  purchase without that text → approve.
- **AU0042** approved: re-quote of the declined AU0037, linked, not a duplicate — state.
- **AU0036** (earlier) step-up: same monitor 25 minutes later — duplicate.

## 4. Session integrity + control (2 min) — SCEN0003

- AU0026 step-up on a new device; customer declines.
- Burst AU0027–AU0030: declined ×4, session banner shows trust degrading.
- AU0031 on the known device asks once ("We're double-checking after unusual activity on
  your card"); customer approves → AU0032 approves: escalate, check once, relax.
- Customer opens the card and **revokes**. Show Viseca rejecting the next request.

## 5. Predictable without AI (30 s)

Flip the chaos toggle off, replay SCEN0004 offline: identical 11 outcomes, latency
unchanged. Show the replay matrix for all 45 purchases: 17 approve / 21 decline / 6 ask /
1 depends, zero hard-rule violations.

## Closing slide

Four boxes, one paper each: deterministic gate (APort Vault), policy compiler with
pre-activation tests (structured decomposition), atomic ledger (ZeroGate), complete
rendering + use-time binding on the step-up (Loopjacking). Then: where this plugs into
AP2/ACP/TAP — one slide, no more.

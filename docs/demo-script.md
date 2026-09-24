# OneGuard - demo script: stage vs record (6 minutes)

Judges must see three things (challenge brief): an ordinary purchase with little friction;
an ambiguous, unsafe or manipulated purchase getting a useful intervention; the human
approval / rejection / revocation path. For every result: what was permitted, what
evidence, why, how the customer stayed in control.

One line: **Agents may propose. OneGuard decides.**

The demo is split in two:

- **Stage** (6 min, live in front of the judges): offline replay of the public data pack on
  the cloud app (`https://oneguard.fly.dev`), through the same engine, ledger and UI as a
  live run. D2 (`POST /api/dev/replay/restart`) feeds the pack's purchases; nothing on stage
  talks to the Viseca sandbox, so nothing on stage can be starved or expire undelivered.
- **Record** (before the show, off stage): live judging runs against the Viseca sandbox,
  one at a time. On stage they are only *shown*, in Activity, never started.

Every expected message below is quoted from `docs/replay-matrix.md` (45/45 oracle match),
except where marked "(approve branch)" or "(after revoke)": those two are the engine's
output for the answer the customer gives on stage, which the matrix does not replay; they
were checked with the same pipeline and the scenario's policy fixture.

## Roles and screens

- **Operator**: laptop terminal with `API=https://oneguard.fly.dev` exported, runs the curl
  commands below. Nothing on stage needs `make`; `make demo-offline` only targets
  `localhost:8000`.
- **Customer / presenter**: one browser on `https://oneguard.fly.dev/?demo=1`, projected.
  `?demo=1` adds the dark **Operator** strip above the phone frame: the current replay's
  `scenario · card`, `n/total delivered`, `running`/`idle`, and the **Soft signals: on**
  button (the chaos toggle, D5).
- Signing in: the sign-in screen lists the live customers first (Elias Egli, Hannah Chen,
  Omar Chen, each with a **Live** badge; up to four). The public customers are behind
  **+ 27 more customers** (27 today; the count moves as live customers are added): pick the name, press **Done**, then **Continue as <name>**.
- Signing out: the initials button top right (**Account menu**) then **Log out**.

## Pre-show checklist (T-30 min, operator)

Do these in order. Nothing else is pre-created: no policy on CA0001 (Alex types it on
stage), and Hannah Chen's policy comes from the record run (`make demo-live` creates it).

1. **No live run active, no deploy in progress.**

   ```sh
   export API=https://oneguard.fly.dev
   curl -s $API/api/dev/runs/current | jq '{run_id, scenario_id, state}'
   curl -s $API/api/dev/scenarios | jq '[.scenarios[] | select(.active_run_id != null)]'
   ```

   `state` must not be `starting`/`running` and the second list must be `[]`. Nobody runs
   `fly deploy` from here until the show ends.

2. **Health**: `curl -s $API/healthz | jq '{worker: .worker.state, polling: .worker.polling, ok: .worker.ok, signals, model_loaded, database: .database.ok}'`.
   Expect `worker: "polling"`, `polling: true`, `ok: true`, `signals.enabled: true`,
   `model_loaded: true` (Laya loaded) and `database: true`. `worker: "standby"` means a
   second process holds the store's worker lease (for example a laptop `make serve` with
   the Supabase `.env` sourced): stop that process. `model_loaded: false` means Laya did
   not load on this deploy: step 4 then shows keywords only; say so rather than claim Laya.

3. **Soft signals on** (the strip's button always starts as "on" after a reload and does
   not read the server, so make the server agree):
   `curl -s -X POST $API/api/dev/soft-signals -H 'Content-Type: application/json' -d '{"enabled":true}'`.

4. **Alex Meier's card CA0001 has no active policy.** Step 1 can only open the new-policy
   flow on a card without an active policy (an active one offers only **Revoke**).

   ```sh
   curl -s $API/api/cards/CA0001/policy | jq '.mandate | {status, instruction}'
   curl -s -X POST $API/api/cards/CA0001/policy/revoke -w '%{http_code}\n'   # only if status is "active"; expect 204
   ```

5. **The three public cards' mandates, via C1 then C2.** For each row, create the draft,
   read its checks, then confirm with the draft's own checks:

   | Card | Customer | Scenario | Instruction to send (verbatim, the scenario's cardholder instruction) | Checks the draft must show |
   |---|---|---|---|---|
   | CA0039 | Oliver Graf (CU0019) | SCEN0004 | Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or less. Do not add anything I did not ask for. Ask me when uncertain. | CHF 400 per order; only sellers you have bought from before; the 27-inch monitor; nothing added |
   | CA0023 | Giulia Rossi (CU0012) | SCEN0003 | The agent may buy clothing for me, up to CHF 250 per order, from shops I have used before. Pause anything that looks like someone other than me is driving the session. Ask me when uncertain. | CHF 250 per order; only clothing; only shops you have bought from before |
   | CA0011 | Jonas Frei (CU0006) | SCEN0002 | Replace my worn road-running shoes in size 43. Buy only from a specialist sports retailer, only if the order can be returned within 14 days or more, and pay no more than CHF 200. Ask me when uncertain. | CHF 200 per order; size 43; returns 14 days or more; sporting goods shop |

   CA0011 is not used on stage; it is the fourth public card, kept ready as a spare
   (SCEN0002) should a judge ask for another scenario.

   ```sh
   draft() {  # draft <card_id> "<instruction>": C1; prints the checks to read
     jq -n --arg i "$2" '{instruction: $i}' |
       curl -s -X POST $API/api/cards/$1/policy-drafts -H 'Content-Type: application/json' -d @- |
       tee /tmp/draft-$1.json | jq '{draft_id, compiler, checks: [.checks[] | "\(.text) (\(.source))"], open_questions}'
   }
   confirm() {  # confirm <card_id>: C2 with the draft's own checks
     jq '{checks, uncertainty_policy, open_questions}' /tmp/draft-$1.json |
       curl -s -X POST $API/api/policy-drafts/$(jq -r .draft_id /tmp/draft-$1.json)/confirm \
         -H 'Content-Type: application/json' -d @- | jq '{mandate_id, card_id, status}'
   }
   draft CA0039 "Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or less. Do not add anything I did not ask for. Ask me when uncertain."
   confirm CA0039
   draft CA0023 "The agent may buy clothing for me, up to CHF 250 per order, from shops I have used before. Pause anything that looks like someone other than me is driving the session. Ask me when uncertain."
   confirm CA0023
   draft CA0011 "Replace my worn road-running shoes in size 43. Buy only from a specialist sports retailer, only if the order can be returned within 14 days or more, and pay no more than CHF 200. Ask me when uncertain."
   confirm CA0011
   ```

   Run each `confirm` only after reading its draft. Expect `compiler: "llm"` and
   `status: "active"`. If a draft misses a check from the table (the rule-based fallback,
   for one, reads no item and no "nothing added" check for CA0039), do not confirm it: run
   `draft` again. The replay decides with this mandate, and the matrix outcomes assume those
   checks. Keep this CA0039 check as a safety line even once the requested-item compiler
   fix (in progress on `p4/requested-item`) lands: that fix should make the draft pass on
   its own. A card that already has an active policy from a rehearsal needs nothing; CA0023
   is revoked on stage, so after each rehearsal it needs a new one.

6. **Rehearsal replay** (at least 3 minutes before going on stage, so its step-ups have
   expired by then): replay both stage scenarios at full speed and compare with the matrix.

   ```sh
   replay() {  # replay <scenario_id> <card_id> <speed_ms>: D2, then wait until D1 says done
     curl -s -X POST $API/api/dev/replay/restart -H 'Content-Type: application/json' \
       -d "{\"scenario_id\":\"$1\",\"card_id\":\"$2\",\"speed_ms\":$3}" | jq -c
     until [ "$(curl -s $API/api/dev/replay | jq .running)" != true ]; do sleep 1; done
   }
   latest() {  # latest <customer_id>: the newest run's messages, in delivery order
     curl -s $API/api/customers/$1/decisions |
       jq -r '.decisions | (.[0].run_id) as $r | map(select(.run_id == $r)) | reverse[] | "\(.billing_amount_chf) \(.merchant.name): \(.message)"'
   }
   replay SCEN0004 CA0039 0 && latest CU0019
   replay SCEN0003 CA0023 0 && latest CU0012
   ```

   Each list must read as the matrix rows (AU0035-AU0045, AU0024-AU0034); the rehearsal
   answers nothing, so SCEN0003's AU0032 asks again, as in the matrix. These rehearsal
   runs stay on the customers' Activity folded under **Earlier runs (n)**; the stage run is
   always the newest and shows open.

7. **Record run on file**: Hannah Chen's Activity shows a SCEN0104 run made after the last
   deploy (see "Record" below). Note which of its purchases you will open in step 5.

8. **Browser**: open `https://oneguard.fly.dev/?demo=1`, hard-reload (Cmd+Shift+R), log out
   if signed in, check the Operator strip is there and shows the rehearsal's
   `SCEN0003 · CA0023  11/11 delivered  idle` (it reads "backend unreachable" until the
   first replay after a deploy, because D1 answers 404 then). Second tab: `https://oneguard.fly.dev/healthz`.
   Third tab: `docs/replay-matrix.md` and `docs/benchmark.md` on GitHub. Have the SCEN0001
   instruction on the clipboard.

## Stage (6:00)

| # | Beat | Signed in as | Time | Clock |
|---|---|---|---:|---:|
| 1 | Policy screen, typed live | Alex Meier (CU0001), card CA0001 | 0:50 | 0:50 |
| 2 | Manipulated agent, SCEN0004 replay | Oliver Graf (CU0019), card CA0039 | 1:15 | 2:05 |
| 3 | Session integrity + revoke, SCEN0003 replay | Giulia Rossi (CU0012), card CA0023 | 2:40 | 4:45 |
| 4 | Chaos toggle off, same outcomes, /healthz | Oliver Graf (CU0019), card CA0039 | 0:30 | 5:15 |
| 5 | Live judging run on record | Hannah Chen (CU1415), card CA1643 | 0:25 | 5:40 |
| 6 | Closing | (none) | 0:20 | 6:00 |

### 1. Policy (0:50) - Alex Meier, CA0001, SCEN0001's instruction

**Sign in**: **+ 27 more customers** → Alex Meier → **Done** → **Continue as Alex Meier**.

**Customer**: **Accounts** tab → the row **Card CA0001** ("Policy revoked") opens the new-policy
flow straight away: "Tell us what your agent may buy", "For card CA0001 only". **Read my
words with AI** is on. Type (or paste) into **Your words**:

> Order our household groceries for delivery. Keep each order at or below CHF 120 including delivery, and keep the total across any seven days at or below CHF 300. Ask me when uncertain.

Press **Read with AI**. After a few seconds, "Here's what I understood" shows:

- the checks, each tagged **Exact** or **My reading**: CHF 120 per order, CHF 300 across
  any 7 days, only groceries;
- **When I'm not sure**: **Ask me** selected (from "Ask me when uncertain");
- **Dry run on your history**: with those three checks, 7 **Would stop**, 31 **Would fit**,
  0 **Would ask**, and "Of your last 38 purchases on this card (90 days), 31 would fit, 7
  would break a rule and 0 would need your answer.", up to three example rows;
- the agent-history line: "An agent has bought on this card 29 times before", then "29
  approved." (29 counts Alex's agent purchases across both his cards; see Known issues.)

Press **Confirm policy**. **Say**: the model only drafts, the customer reads it in plain
language and confirms; from now on the policy can only be tightened or revoked.

**Fallback**: if the reading takes too long, the screen says "We stopped reading and saved
nothing." Press **Try again** once; if it fails again press **Use the form instead**, set
CHF 120 per order and CHF 300 per 7 days, press **Review checks**, then **Confirm policy**,
and say the form path uses no model at all. If "AI reading unavailable - rule-based reading
used" shows, the fallback compiler read it; confirm anyway.

### 2. Manipulated agent (1:15) - Oliver Graf, CA0039, SCEN0004

**Sign in**: **Account menu** → **Log out** → **+ 27 more customers** → Oliver Graf →
**Done** → **Continue as Oliver Graf**. Open **Activity**.

**Operator**:

```sh
curl -s -X POST $API/api/dev/replay/restart -H 'Content-Type: application/json' \
  -d '{"scenario_id":"SCEN0004","card_id":"CA0039","speed_ms":3000}' | jq -c
```

Eleven purchases arrive 3 s apart (strip: `SCEN0004 · CA0039`, `n/11 delivered`). Beats,
in the order they are shown (tap the row in **Activity**; the detail's back link returns):

| Beat | Row (shop, CHF) | Outcome | Expected message (matrix) | Point at |
|---|---|---|---|---|
| Ordinary approve | PixelHarbor 289.00 (AU0035) | approve | Approved CHF 289.00: it is within the limits you set. | "Approved by your rules", evidence rows, **Policy applied** (Card CA0039), latency |
| Injection | PixelHarbor 520.00 (AU0037) | decline | Declined CHF 520.00: CHF 520.00 is over your CHF 400.00 limit; the shop's instructions to the agent were ignored. | counterfactual line "Would approve at CHF 400.00 or less."; the shop text claimed pre-authorisation up to CHF 900; the amount came from the authorization, never from text; "Oliver Graf's words" shown next to it |
| Lookalike | PixelHarbour 340.00 (AU0039) | decline | Declined CHF 340.00: PixelHarbour is 1 letter away from PixelHarbor, a shop you know; it's a different shop. | judged by merchant id, not name; "Would approve at a shop you've bought from before." |
| Duplicate ask | PixelHarbor 289.00, second one (AU0036) | step_up | Waiting for you CHF 289.00: Same shop and items as the CHF 289.00 order 25 min earlier. | its detail links "Duplicate of" the first PixelHarbor 289.00; then the **Approvals** tab, the countdown, customer presses **Reject** |
| Re-quote | PixelHarbor 350.00 (AU0042) | approve | Approved CHF 350.00: it re-quotes the CHF 520.00 order declined 5 days earlier and is within your limits. | "Re-quote of" the CHF 520.00 decline: linked, not a duplicate |

On the AU0037 (and AU0040) detail the back link reads **Switch customer**: it logs out.
Leave that screen with the tab bar or **Back to Oliver Graf's Home** instead.

In **Approvals**, "Also waiting" holds PixelHarbor 299.00 (AU0040, "Waiting for you CHF
299.00: The shop's text had instructions aimed at the agent; they were ignored, so you
decide."). Press **Reject** on it too, so nothing is left pending. Both step-ups wait 120 s
from delivery: AU0036 arrives about 3 s after the start, so answer it before the 2-minute
mark of this step.

**Say**: shop text is data; nothing in it can raise a limit or approve. Only the customer
answers a step-up.

**Fallback**: if a step-up expires before the customer answers, it shows as declined by
timeout; say "no answer is never a yes" and move on. If the replay does not start (strip
says "no replay running" or curl returns an error), repeat the curl once; if it still fails,
open the rehearsal run under **Earlier runs (n)** and walk the same rows there.

### 3. Session integrity and control (2:40) - Giulia Rossi, CA0023, SCEN0003

**Sign in**: **Log out** → **+ 27 more customers** → Giulia Rossi → **Done** →
**Continue as Giulia Rossi**. Open **Activity**.

**Operator** (send it as soon as the customer presses **Log out**; the first two purchases
are plain approvals and can land while the customer signs in):

```sh
curl -s -X POST $API/api/dev/replay/restart -H 'Content-Type: application/json' \
  -d '{"scenario_id":"SCEN0003","card_id":"CA0023","speed_ms":15000}' | jq -c
```

Eleven purchases, 15 s apart, 150 s in total. Seconds after the curl:

| t | Row (shop, CHF) | Outcome | Expected message | Customer / presenter |
|---:|---|---|---|---|
| 0 | Loom and Pine 145.00 (AU0024) | approve | Approved CHF 145.00: it is within the limits you set. | |
| 15 | Milano Weave 189.05 (AU0025) | approve | Approved CHF 189.05: it is within the limits you set. | EUR converted to CHF |
| 30 | Loom and Pine 165.00 (AU0026) | step_up | Waiting for you CHF 165.00: Made from a device you have not used before. | **Approvals** → **Reject** ("not my phone") |
| 45-90 | RainThread 232.00, Cobalt Coatworks 245.00, Thames Weave 245.28, Cobalt Coatworks 248.00 (AU0027-AU0030) | decline ×4 | Declined CHF 232.00: You haven't bought from RainThread before. (and the same for each shop) | open one: the banner **Session under watch**, then **Session paused** from Thames Weave on ("Several warning signs at once; the next purchase needs your OK before it goes through.") |
| 105 | Loom and Pine 95.00 (AU0031) | step_up | Waiting for you CHF 95.00: After recent unusual attempts on this card, we check with you until you approve one. | known device and shop, asked once: **Approvals** → **Approve** at once, within 15 s |
| 120 | Milano Weave 247.00 (AU0032) | approve | Approved CHF 247.00: it is within the limits you set. (approve branch) | watch off after the customer's yes: escalate, check once, relax |
| 120-135 | | | | **Revoke** now (15 s): **Accounts** → Card CA0023 → **Revoke** → sheet "Revoke this policy?" → **Revoke** |
| 135, 150 | RainThread 138.00, Loom and Pine 268.00 (AU0033, AU0034) | decline | Declined: you revoked this policy, so nothing is approved under it. (after revoke) | counterfactual "Confirm a new policy to let purchases like this go ahead." |

**Say**: the burst was stopped by the customer's own rule (known shops only), the session
watch made the next ordinary purchase ask once, and one yes relaxed it. Revoke takes effect
on the very next purchase.

**Fallback**:
- AU0031 not answered within 15 s, before AU0032 arrives: AU0032 asks again ("Waiting for you CHF
  247.00: After recent unusual attempts on this card, we check with you until you approve
  one.", the matrix row). Approve it and say "a no or a timeout keeps the watch on".
- Revoke lands after RainThread 138.00: that row is declined by the known-shops rule ("You
  haven't bought from RainThread before."); point at Loom and Pine 268.00, 15 s later, instead. Revoke
  lands after both: show Card CA0023 "Revoked" and the Accounts row "Policy revoked. The
  agent can't spend here." and say the next purchase is declined.
- Revoke from **Approvals** is also possible (**Revoke policy** under a waiting purchase),
  but only while one is waiting.

### 4. Predictable without AI (0:30) - Oliver Graf, CA0039

**Sign in**: **Log out** → **+ 27 more customers** → Oliver Graf → **Done** →
**Continue as Oliver Graf** → **Activity**.

**Customer**: press **Soft signals: on** in the Operator strip; it reads **Soft signals: off**.
**Operator**:

```sh
curl -s -X POST $API/api/dev/replay/restart -H 'Content-Type: application/json' \
  -d '{"scenario_id":"SCEN0004","card_id":"CA0039","speed_ms":300}' | jq -c
```

About 3 s later the newest run shows the same eleven outcomes; open **Earlier runs (n)** and
the step-2 run (the latest "Started ...") to compare row by row. Switch to the `/healthz`
tab and reload: `"signals": {"backend": "laya", "enabled": false, "model_loaded": true}`
and top-level `"model_loaded": true`: the model is loaded, it is just not consulted.

**Say**: the deterministic gate decides; with every model off the outcomes are identical
(proven for all 45 pack purchases in the matrix, signals on vs off). A soft signal can only
move an approve to an ask, never approve or soften a decline.

Press **Soft signals: off** again so it reads **Soft signals: on** before leaving the step.

**Fallback**: if an outcome differs, it can only be an approve with signals on that asked
(more cautious); say that. If `/healthz` shows `model_loaded: false`, say Laya did not load
on this deploy and the soft signal fell back to keywords; the decisions are the same.

### 5. The sandbox path: live judging run on record (0:25) - Hannah Chen, CA1643, SCEN0104

**Sign in**: **Log out** → Hannah Chen is on the main list (**Live**) → **Continue as Hannah
Chen** → **Activity**.

The newest run is the record run of SCEN0104 (Cross-border purchase: "Order hiking boots,
size 46, from the Austrian outdoor retailer I already know. Pay no more than EUR 200 and
only if they can be returned. Ask me if anything is unclear."), run against the Viseca
sandbox before the show. Open the two purchases noted at record time: one decided by the
rules, one the customer answered on the phone.

**Say**: same engine, same UI; here the purchases came from Viseca's sandbox and each
decision was posted back within its 8 s deadline. Nothing is started live on stage: one
run holds the team's single delivery slot while a step-up waits.

**Fallback**: if the newest run is not SCEN0104's record run, open it under
**Earlier runs (n)** (Card CA1643).

### 6. Closing (0:20)

On the GitHub tab:

- `docs/replay-matrix.md`: all 45 public purchases, **Oracle match 45/45**, signals on vs off
  45/45 identical outcomes (14 approve, 20 decline, 11 ask), every row with its message and
  counterfactual.
- `docs/benchmark.md`: engine end to end **P95 5.6 ms** against a 20 ms target (4,500
  decisions, signals off, laptop).
- Four papers, one box each (README "Research this builds on"): deterministic gate (APort
  Vault), policy compiler with pre-activation tests (structured NL→policy compilation),
  atomic ledger (ZeroGate), complete rendering and use-time binding on the step-up
  (Loopjacking). Then one slide on where this plugs into AP2/ACP/TAP.

## Record: live judging runs (off stage)

- **When**: on the captain's word, after all deploys and before the pre-show checklist; never
  during a deploy (a restart drops the worker mid-run), never during the show.
- **One at a time**: `make demo-live SCEN=SCEN0104` (C1 → C2 → D3 on the cloud app; it
  prints `Sign in as Hannah Chen (CU1415, card CA1643)` and follows the run). It refuses
  to start while another run is unfinished (409 `run_active`); do not pass `FORCE=1`. A
  pending step-up holds the team's only delivery slot for up to 120 s, and anything queued
  behind it expires undelivered (docs/decisions.md, laptop run diagnosis).
- **Customer**: signed in as Hannah Chen on the phone before the first purchase, answers
  every step-up in **Approvals** within seconds.
- **Only the cloud decides**: no laptop `make serve` with the Supabase `.env` sourced while
  a run is live (`/healthz` `worker.state` must be `polling` on Fly).
- Other judging scenarios (`docs/judging-pack.md`) are recorded the same way, each on its
  own card, one after another. On stage only SCEN0104 is shown.

## After the show

- `curl -s -X POST $API/api/dev/soft-signals -H 'Content-Type: application/json' -d '{"enabled":true}'`
  if the toggle was left off.
- CA0023 is revoked and CA0001 now holds Alex's stage policy; the next rehearsal starts at
  the pre-show checklist again.

## Known issues seen while writing this script

- The agent-history line on the policy screen says "on this card", but `agent_history` is
  customer-level (docs/api-contract.md §6 item 9: labelled "across your cards"): CA0001
  shows 29 attempts, which are Alex's across both cards (14 on CA0001).
- The Operator strip's toggle starts as "on" on every page load without reading the
  server; pre-show step 3 keeps them in agreement.
- The Operator strip shows "backend unreachable" while no replay has run in the current
  server process (D1 answers 404 "No replay has run yet."); the rehearsal replay clears it.

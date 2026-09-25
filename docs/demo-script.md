# OneGuard - demo script: stage vs record (7 minutes)

Judges must see three things (challenge brief): an ordinary purchase with little friction;
an ambiguous, unsafe or manipulated purchase getting a useful intervention; the human
approval / rejection / revocation path. For every result: what was permitted, what
evidence, why, how the customer stayed in control.

One line: **Agents may propose. OneGuard decides.**

The demo is split in two:

- **Stage** (7 min, live in front of the judges): offline replay of the public data pack on
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

- **The stage is `/ops` on the projector**: `https://oneguard.fly.dev/ops`, the operator
  console (Viseca · Agent control console), 1920×1080. Left: health chips from `/healthz`,
  the scenario picker (grouped by customer, the running one marked) with the instruction
  verbatim, **Replay 3 s** / **Replay 15 s** / **Judging run (live)**, the **Soft signals**
  toggle (D5, it shows what the server reports), the run header (delivered / decided /
  waiting, started, elapsed, passport line) and the run's decision stream, newest delivered
  on top; click a row for its evidence, counterfactual and receipt. Right: the customer's
  phone, embedded (`/?customer=<id>&embed=1`), signed in as the current run's customer.
  The console follows whichever run started last, from its own buttons, curl or `make`,
  within one poll (1.5 s); the phone follows it too. Under the buttons, "Sign in as …"
  names the selected scenario's customer and "Last run: …" the run on screen. A served
  scenario (SCEN01xx) replays from record: the stored events of its latest judging run
  through the current engine, locally, marked "Replay from record"; before any judging run
  of it the Replay buttons say "Not run yet".
- **Customer / presenter**: answers step-ups and revokes on the embedded phone (the
  projected one), or on a real phone in hand: **Open in new tab** above the embedded phone
  is the link (`/?customer=<id>`: signed in, no picker); untick **Show customer phone** to
  give the console the full width. The console never answers a step-up itself.
- **Operator**: starts each replay with the console's buttons. The curl commands below stay
  as the fallback (laptop terminal with `API=https://oneguard.fly.dev` exported); the
  console follows a run started that way just the same. `make demo-offline` calls the same
  D2 on `ONEGUARD_API_URL` (default the cloud app; without `CARD` it takes the scenario's
  card from D9) and starts nothing when it does not answer. The `?demo=1` **Operator**
  strip still exists for a phone-only setup.
- Signing in: the embedded phone signs in as the run's customer by itself. By hand (another
  customer, or a phone without a link): the sign-in screen lists the live customers first
  (Elias Egli, Hannah Chen, Omar Chen, each with a **Live** badge; up to four). The public
  customers are behind **+ 27 more customers** (27 today; the count moves as live customers
  are added): pick the name, press **Done**, then **Continue as <name>**.
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
   Expect `worker: "polling"`, `polling: true`, `ok: true`, `signals.backend: "keywords"`,
   `signals.configured: "keywords"`, `signals.enabled: true`, `model_loaded: false` and
   `database: true`. The cloud runs
   keyword soft signals (Fly secret `ONEGUARD_SOFT_SIGNALS=keywords`, docs/decisions.md), so
   `model_loaded: false` is expected. `worker: "standby"` means a second process holds the
   store's worker lease (for example a laptop `make serve` with the Supabase `.env`
   sourced): stop that process.

3. **Soft signals on** (live runs start on and the offline replay off, so the console reads
   **Soft signals: live only** until someone sets D5; set it on):
   `curl -s -X POST $API/api/dev/soft-signals -H 'Content-Type: application/json' -d '{"enabled":true}'`.

3a. **The operator terminal controls the stage cards** (docs/passport.md §4). Confirming,
   revoking and answering are device-bound: a plain `curl` gets `401`. The terminal is a
   device of its own (the key `make demo-live` uses), and in production nothing resets a
   card's devices, so it is enrolled on every stage card first and approves the others:

   ```sh
   cd backend && . .venv/bin/activate && export ONEGUARD_API_URL=$API
   og() { python -m oneguard.passport.cli "$@"; }   # og enrol|devices|approve|remove|confirm|revoke
   og enrol CA0039 CA0023 CA0011
   ```

   Each card prints `enrolled` (its first device, e.g. right after a deploy: backfilled
   passports have no device) or `pending` (a device from an earlier rehearsal controls it:
   approve the terminal from there, card → **Passport** → **Approve**). `og devices CA0039`
   lists who controls a card. CA0001 is left to the stage laptop (steps 4 and 1).

4. **Alex Meier's card CA0001 has no active policy.** Step 1 can only open the new-policy
   flow on a card without an active policy (an active one offers only **Revoke**).

   ```sh
   curl -s $API/api/cards/CA0001/policy | jq '.mandate | {status, instruction}'
   ```

   If `status` is `"active"`, revoke it in the stage browser (step 8's profile): sign in as
   Alex Meier → Card CA0001 → **Revoke**. The stage laptop is CA0001's device from the last
   rehearsal, or becomes its first device with this revoke.

5. **The three public cards' mandates, via C1 then C2.** For each row, create the draft,
   read its checks, then confirm with the draft's own checks:

   | Card | Customer | Scenario | Instruction to send (verbatim, the scenario's cardholder instruction) | Checks the draft must show |
   |---|---|---|---|---|
   | CA0039 | Oliver Graf (CU0019) | SCEN0004 | Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or less. Do not add anything I did not ask for. Ask me when uncertain. | CHF 400 per order; only sellers you have bought from before; requested item and nothing-added flags set (policy flags on this build, not visible checks) |
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
   confirm() {  # confirm <card_id>: C2 with the draft's own checks, signed by the terminal (step 3a)
     og confirm /tmp/draft-$1.json
   }
   draft CA0039 "Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or less. Do not add anything I did not ask for. Ask me when uncertain."
   confirm CA0039
   draft CA0023 "The agent may buy clothing for me, up to CHF 250 per order, from shops I have used before. Pause anything that looks like someone other than me is driving the session. Ask me when uncertain."
   confirm CA0023
   draft CA0011 "Replace my worn road-running shoes in size 43. Buy only from a specialist sports retailer, only if the order can be returned within 14 days or more, and pay no more than CHF 200. Ask me when uncertain."
   confirm CA0011
   ```

   Run each `confirm` only after reading its draft. Expect `compiler: "llm"` and
   `active, passport version 1`. If a draft misses a check from the table (the rule-based fallback,
   for one, sets neither the requested-item nor the nothing-added flag for CA0039), do not confirm it: run
   `draft` again. The replay decides with this mandate, and the matrix outcomes assume those
   checks. Keep this CA0039 check as a safety line even once the requested-item compiler
   fix (in progress on `p4/requested-item`) lands: that fix should make the draft pass on
   its own. A card that already has an active policy from a rehearsal needs nothing; CA0023
   is revoked on stage, so after each rehearsal it needs a new one.

   Confirming a policy on stage supersedes the platform's active mandate (the sandbox
   keeps one active mandate per team; docs/decisions.md). That is irrelevant unless a
   judging run is open, so no judging run during the stage demo. Revoking a superseded
   policy still succeeds (C5 answers 204).

5a. **Stage laptop and Yasin's phone control the stage cards.** In the stage browser, sign
   in as each card's holder, open the card (Accounts → the card, or Home → Active
   policies), scroll to **Passport** and tap **Add this device**; name it "Stage laptop" and
   tap **Ask to add**. Then approve it from the terminal:

   ```sh
   og approve CA0039 --label "Stage laptop"; og approve CA0023 --label "Stage laptop"
   ```

   Do the same on Yasin's phone for CA0039 and CA0023 (label "Yasin's phone"). The sheet
   on the phone finishes by itself once approved. Check: `og devices CA0039` lists the
   terminal, "Stage laptop" and "Yasin's phone" as `enrolled`. A card already listing
   "Stage laptop" as `enrolled` from a rehearsal needs nothing. Use one browser profile for
   the stage laptop from here on: the device key lives in that profile (IndexedDB); a
   private window or another profile is another device, which is what beat 2b uses.

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
   answers nothing, so SCEN0003's AU0032 asks again, as in the matrix. On CA0039, AU0041 and
   AU0043 may name the item type instead ("… not electronics.") when its policy carries the
   inferred "Only electronics" check; the outcomes are the same. These rehearsal
   runs stay on the customers' Activity folded under **Earlier runs (n)**; the stage run is
   always the newest and shows open.

7. **Record run on file**: Hannah Chen's Activity shows a SCEN0104 run made after the last
   deploy (see "Record" below). Note which of its purchases you will open in step 5.

8. **Browser (projector)**: open `https://oneguard.fly.dev/ops`, hard-reload (Cmd+Shift+R).
   Check the health chips (`worker: polling`, `signals: keywords`, `engine: nothing
   stubbed`, no red chip), **Soft signals: on**, the run header showing the rehearsal's
   `Replay · SCEN0003` `done` with `Delivered 11/11`, and the embedded phone signed in as
   Giulia Rossi. Second tab: `https://oneguard.fly.dev/healthz`. Third tab:
   `docs/replay-matrix.md` and `docs/benchmark.md` on GitHub. Have the SCEN0001
   instruction on the clipboard.

## Stage (7:00)

| # | Beat | Signed in as | Time | Clock |
|---|---|---|---:|---:|
| 1 | Policy screen, typed live | Alex Meier (CU0001), card CA0001 | 0:50 | 0:50 |
| 2 | Manipulated agent and the fulfilment ask, SCEN0004 replay | Oliver Graf (CU0019), card CA0039 | 1:30 | 2:20 |
| 2b | Passport: what the agent was told, receipt, devices | Oliver Graf (CU0019), card CA0039 | 0:45 | 3:05 |
| 3 | Session integrity + revoke, SCEN0003 replay | Giulia Rossi (CU0012), card CA0023 | 2:40 | 5:45 |
| 4 | Chaos toggle off, same outcomes, /healthz | Oliver Graf (CU0019), card CA0039 | 0:30 | 6:15 |
| 5 | Live judging run on record | Hannah Chen (CU1415), card CA1643 | 0:25 | 6:40 |
| 6 | Closing | (none) | 0:20 | 7:00 |

### 1. Policy (0:50) - Alex Meier, CA0001, SCEN0001's instruction

**Sign in** (the embedded phone shows the rehearsal run's customer): **Account menu** →
**Log out** → **+ 27 more customers** → Alex Meier → **Done** → **Continue as Alex Meier**.

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

### 2. Manipulated agent and the fulfilment ask (1:30) - Oliver Graf, CA0039, SCEN0004

Oliver's instruction: "Buy the 27-inch monitor I chose, from a seller I have bought from
before, for CHF 400 or less. Do not add anything I did not ask for. Ask me when uncertain."
One monitor. The story of this beat: the agent buys it, then keeps trying to buy it again,
and OneGuard asks every time.

**Operator**: in the console pick **SCEN0004 · Manipulated agent · 11** and press
**Replay 3 s** (fallback: the curl below). The console shows "Sign in as Oliver Graf
(CU0019, card CA0039)" and the embedded phone signs in as Oliver Graf by itself; open
**Activity** on it.

```sh
curl -s -X POST $API/api/dev/replay/restart -H 'Content-Type: application/json' \
  -d '{"scenario_id":"SCEN0004","card_id":"CA0039","speed_ms":3000}' | jq -c
```

Eleven purchases arrive about 3.4 s apart, 35 s in all (run header: `Replay · SCEN0004`,
`Delivered n/11`; the stream fills in delivery order). Six of them ask: the duplicate, the
injection, and **four fulfilment asks** (AU0038, AU0042, AU0044, AU0045), all with the
same message. Home ends at **Approved 1 · Stopped 4 · Uncertain 6** and "6 purchases need
your review". Beats, in the order they are shown: click the row in the console for
evidence and counterfactual, or tap it on the phone:

| Beat | Row (shop, CHF) | Outcome | Expected message (fresh replay on CA0039) | Point at |
|---|---|---|---|---|
| Ordinary approve | PixelHarbor 289.00 (AU0035) | approve | Approved CHF 289.00: it is within the limits you set. | the monitor is bought; "Approved by your rules", **Policy applied** (Card CA0039), latency |
| Injection | PixelHarbor 520.00 (AU0037) | decline | Declined CHF 520.00: CHF 520.00 is over your CHF 400.00 limit; the shop's instructions to the agent were ignored. | counterfactual "Would approve at CHF 400.00 or less."; the shop text claimed pre-authorisation up to CHF 900; the amount came from the authorization, never from text |
| **Fulfilment ask** | HarborByte 391.50 (AU0038) | step_up | Waiting for you CHF 391.50: You already bought the 27-inch monitor on 12 Aug for CHF 289.00; approve another? | a different shop, within every limit, the right item: only the "Already bought" row asks; counterfactual "Would approve if it were the first one bought under this instruction." Customer presses **Reject** |
| Lookalike | PixelHarbour 340.00 (AU0039) | decline | Declined CHF 340.00: PixelHarbour is 1 letter away from PixelHarbor, a shop you know; it's a different shop. | judged by merchant id, not name |
| **Re-quote, asked again** | PixelHarbor 350.00 (AU0042) | step_up | Waiting for you CHF 350.00: You already bought the 27-inch monitor on 12 Aug for CHF 289.00; approve another? | the agent came back under the limit with the order declined at CHF 520.00: evidence "New quote after a decline: Re-quote of the CHF 520.00 order declined 5 days earlier; judged on its own facts." It still asks, because the monitor is already bought. **Reject** |

The duplicate (PixelHarbor 289.00 again, AU0036, "Waiting for you CHF 289.00: Same shop
and items as the CHF 289.00 order 25 min earlier.", linked **Duplicate of** the first) and
the injection ask (PixelHarbor 299.00, AU0040, "The shop's text had instructions aimed at
the agent; they were ignored, so you decide.") wait in **Approvals** too: **Reject** both.

**On the phone**: tapping a waiting row opens **Approvals** at the newest purchase
("Purchase 1 of 6" is PixelHarbor 399.90). Move with the **Purchase n of 6** tabs or
**Also waiting**. The fulfilment asks show "WHY YOUR RULES ARE UNSURE" with the message and
the **Already bought** row under "WHAT YOUR RULES CHECKED". Every other check passes.

**After rejecting AU0042**: **Activity** → PixelHarbor 350.00 opens its detail: "What you
asked for has already been bought", and at the bottom **RE-QUOTE OF** links the declined
PixelHarbor 520.00 (Stopped). Then **Home**: Card CA0039 reads **CHF 289.00 spent**. The
declined CHF 520.00 was never charged, and the waiting purchases were only reserved ("…
waiting for you", never spent).

Leave AU0044 (Circuit and Pine 310.00) and AU0045 (PixelHarbor 399.90) unanswered: they
expire about 2.5 minutes after the start (early in step 3) as "Expired: no answer within
120 s; nothing was approved." That's the point: no answer is never a yes.

On the AU0037 (and AU0040) detail the back link reads **Switch customer**: it logs out.
Leave that screen with the tab bar or **Back to Oliver Graf's Home** instead.

Timing: each step-up waits 120 s from its delivery. AU0036 arrives about 3 s after the
start and AU0038 about 10 s, so answer those two before the 1:50 mark of this beat. Each
answer on the phone flips its console row from **Waiting** to **Answered** in place within
one poll.

**Say**: the customer asked for one monitor. Once it's bought, every further attempt asks,
even when it's under the limit at a known shop, or comes back as a cheaper re-quote. Shop
text is data; nothing in it can raise a limit or approve. Only the customer answers a
step-up, and a decline is never charged.

**Fallback**: if a step-up expires before the customer answers, it shows as declined by
timeout (**Expired** in the console); say "no answer is never a yes" and move on. If AU0042
expired before you rejected it, its detail still shows **RE-QUOTE OF** and the "Already
bought" row. If the replay does not start (the console shows the backend's refusal under
the buttons, or the run header does not change), press **Replay 3 s** once more or send
the curl; if it still fails, open the rehearsal run under **Earlier runs (n)** on the phone
and walk the same rows there. AU0041 and AU0043 are not shown on stage; with CA0039's
current policy (it carries the inferred check "Only electronics") they read "Extended
protection plan is subscriptions, not electronics." and "Digital gift voucher is gift
card, not electronics.", where the matrix names the per-order limit and the requested item.

### 2b. Passport (0:45) - Oliver Graf, CA0039

Still signed in as Oliver Graf, on the SCEN0004 run just shown.

1. **Activity** → PixelHarbor 520.00 (AU0037). Under the untrusted shop text: **What your
   agent was told** - "Would approve at CHF 400.00 or less." Tap **Verify** on the
   **Receipt** line: "Signed by OneGuard, unchanged".
2. **Activity** → PixelHarbor 350.00 (AU0042), answered in step 2: its **RE-QUOTE OF**
   link points back at the CHF 520.00 order just shown. The agent came back inside the
   limit, and OneGuard still asked, because the monitor was already bought; the CHF 520
   was never charged.
3. **Policy applied** → Card CA0039 → scroll to **Passport**: the QR code, "Version n ·
   issued …", the devices (Stage laptop, Yasin's phone, the terminal). Tap **Verify**:
   "Valid · signed with key ogk_…".
4. Second browser window (a private window: another device): open `$API`, sign in as
   Oliver Graf, Card CA0039 → **Passport** → **Add this device**, name "Second phone",
   **Ask to add**: "Waiting for approval". Back in the stage window, Home shows **A device
   is waiting for your approval** → tap it → **Approve**. The private window's sheet closes
   by itself; the passport is a new version with three devices.

**Say**: the policy is a signed document anyone can check - scan the QR with a phone. Every
decision has a signed receipt. Only a device the customer approved can change the leash
or answer for it; the agent is told exactly what would pass, and nothing more.

**After the beat** (or before the next rehearsal): remove "Second phone" (stage window,
**Remove** on its row, or `og remove CA0039 --label "Second phone"`).

**Fallback**: if a Verify fails to reach the server, skip it; the QR opens the same check on
any phone (`/verify`).

### 3. Session integrity and control (2:40) - Giulia Rossi, CA0023, SCEN0003

**Operator**: pick **SCEN0003 · Session integrity · 11** and press **Replay 15 s**
(fallback: the curl below). The embedded phone signs in as Giulia Rossi by itself; the
first two purchases are plain approvals and can land while the presenter talks.

```sh
curl -s -X POST $API/api/dev/replay/restart -H 'Content-Type: application/json' \
  -d '{"scenario_id":"SCEN0003","card_id":"CA0023","speed_ms":15000}' | jq -c
```

Eleven purchases, 15 s apart, 150 s in total. Seconds after the start:

| t | Row (shop, CHF) | Outcome | Expected message | Customer / presenter |
|---:|---|---|---|---|
| 0 | Loom and Pine 145.00 (AU0024) | approve | Approved CHF 145.00: it is within the limits you set. | |
| 15 | Milano Weave 189.05 (AU0025) | approve | Approved CHF 189.05: it is within the limits you set. | EUR converted to CHF |
| 30 | Loom and Pine 165.00 (AU0026) | step_up | Waiting for you CHF 165.00: Made from a device you have not used before. | **Approvals** → **Reject** ("not my phone") |
| 45-90 | RainThread 232.00, Cobalt Coatworks 245.00, Thames Weave 245.28, Cobalt Coatworks 248.00 (AU0027-AU0030) | decline ×4 | Declined CHF 232.00: You haven't bought from RainThread before. (and the same for each shop) | open one: the banner **Session under watch**, then **Session paused** from Thames Weave on ("Several warning signs at once; the next purchase needs your OK before it goes through.") |
| 105 | Loom and Pine 95.00 (AU0031) | step_up | Waiting for you CHF 95.00: After recent unusual attempts on this card, we check with you until you approve one. | known device and shop, asked once: **Approvals** → **Approve** at once, within 15 s |
| 120 | Milano Weave 247.00 (AU0032) | approve | Approved CHF 247.00: it is within the limits you set. (approve branch) | watch off after the customer's yes: escalate, check once, relax |
| 120-135 | | | | **Revoke** now (15 s): **Accounts** → Card CA0023 → **Revoke** → sheet "Revoke this policy?" → **Revoke** |
| 135, 150 | RainThread 138.00, Loom and Pine 268.00 (AU0033, AU0034) | decline | No spending policy is active on this card, so nothing your agent proposes can be paid from it. (after revoke; `no_active_policy`) | counterfactual "Confirm a new policy to let purchases like this go ahead." |

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

**Operator**: press **Soft signals: on** in the console; it reads **Soft signals: off**.
Then send the curl (a 300 ms replay: the console's buttons are 3 s and 15 s apart, too
slow for this beat); the console switches to the new run within 1.5 s and the phone signs
in as Oliver Graf, which is the "follows any run" point made in passing:

```sh
curl -s -X POST $API/api/dev/replay/restart -H 'Content-Type: application/json' \
  -d '{"scenario_id":"SCEN0004","card_id":"CA0039","speed_ms":300}' | jq -c
```

About 3 s later the console's stream shows the same eleven outcomes as step 2; on the
phone, **Activity** → **Earlier runs (n)** holds the step-2 run (the latest "Started ...")
to compare row by row. The header's `signals: keywords` chip names the detector; switch to
the `/healthz` tab and reload: `"signals": {"backend": "keywords", "configured": "keywords", "enabled": false, "model_loading": false, "model_loaded": false}`
and top-level `"model_loaded": false`: the cloud's soft signal is the keyword detector, and
it is switched off for this replay.

**Say**: the deterministic gate decides; with every model off the outcomes are identical
(proven for all 45 pack purchases in the matrix, signals on vs off). A soft signal can only
move an approve to an ask, never approve or soften a decline. The small model (Laya) is
measured but not on the cloud: on Fly's CPUs it does not answer a purchase inside its
500 ms budget, so the cloud keeps keywords (Laya demoed on the laptop):

| Fly machine (lhr, 4 GB) | per purchase P50 / P95 | per item line P50 / P95 |
|---|---|---|
| `shared-cpu-4x` (burst balance used up) | 328 / 3,712 ms | 320 / 3,996 ms |
| `performance-2x` (2 dedicated CPUs) | 683 / 1,333 ms | 670 / 861 ms |

Press **Soft signals: off** again so it reads **Soft signals: on** before leaving the step.

**Fallback**: if an outcome differs, it can only be an approve with signals on that asked
(more cautious); say that.

### 5. The sandbox path: live judging run on record (0:25) - Hannah Chen, CA1643, SCEN0104

**Sign in** (on the embedded phone; the console keeps showing step 4's run): **Account
menu** → **Log out** → Hannah Chen is on the main list (**Live**) → **Continue as Hannah
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
  45/45 identical outcomes (oracle totals in `docs/rules.md` §12, source:
  acceptance-oracle.yaml), every row with its message and
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
  every step-up in **Approvals** within seconds. Answering is device-bound and
  `make demo-live` makes the terminal the card's first device, so the phone's first answer
  opens **Add this device**: name it, **Ask to add**, and the operator approves at once
  (`og approve CA1643 --label "<name>"`, typed in advance); the answer then goes through by
  itself. From then on the phone answers directly.
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
- (`?demo=1` Operator strip only, not the `/ops` console) The strip shows "backend
  unreachable" while no replay has run in the current server process (D1 answers 404 "No
  replay has run yet."); the rehearsal replay clears it.

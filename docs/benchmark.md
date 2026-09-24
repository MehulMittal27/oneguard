# OneGuard - Engine benchmark

Lives at `docs/benchmark.md` · produced by `backend/scripts/bench_engine.py` · target from
docs/team-plan.md P2-3: end-to-end P95 < 20 ms without signals.

## Run

| | |
|---|---|
| Engine code | `a61c1a7` (main at the time of the run; the script commit adds no engine change) |
| Date | 2026-09-24, 19:04 CEST |
| Machine | Apple M5 Pro, 15 cores, 24 GB RAM, macOS 26.5.2 arm64 (laptop; background load and power state not controlled) |
| Python | 3.12.13 (uv), SQLAlchemy SQLite driver |
| Laya | `laya==0.3.20`, torch 2.14.0 on MPS, checkpoint `laya-typed-decisions` |

## Reproduce

```sh
cd backend && . .venv/bin/activate
python scripts/bench_engine.py                        # engine: 45 events × 100, signals off
pip install -e ".[dev,signals]"                       # once, for the Laya run
ONEGUARD_SOFT_SIGNALS=laya python scripts/bench_engine.py --laya   # soft signal, × 10
```

## 1. Engine, signals off: PASS

Every event of the 5 scenarios (45) goes through `pipeline.decide_event` with the
registered engine functions (the script exits if any is stubbed or missing) and the
scenario's policy fixture (`backend/tests/fixtures/policies`). The ledger is a
`StoreLedger` on a fresh temp SQLite file per scenario per repetition, so every
`ledger.record` is an insert, never a redelivery; history is a seeded temp SQLite loaded
once; `ONEGUARD_DATABASE_URL` is unset (never Supabase). 3 untimed warm-up repetitions,
then 100 timed: 4,500 decisions. Outcomes are checked identical across repetitions.

Each stage is `time.perf_counter` around the call. **End to end is the `decide_event`
wall time**, so it also covers what sits between the stages (`add_ledger_results`, model
copies, building the API `Decision`). `ledger.get` sums both lookups per decision (the
redelivery check and the one inside `record`); `ledger.record` includes its own.

| stage | n | P50 ms | P95 ms | max ms |
|---|---:|---:|---:|---:|
| build_facts | 4500 | 0.085 | 0.129 | 0.404 |
| evaluate_rules | 4500 | 0.084 | 0.134 | 0.516 |
| protections | 4500 | 0.044 | 0.634 | 32.039 |
| warning_signs | 4500 | 0.015 | 0.018 | 0.123 |
| decide | 4500 | 0.015 | 0.021 | 7.077 |
| explain | 4500 | 0.206 | 0.411 | 76.388 |
| ledger.get | 4500 | 0.304 | 1.294 | 95.160 |
| ledger.view | 4500 | 0.826 | 2.193 | 93.488 |
| ledger.record | 4500 | 0.935 | 1.552 | 93.357 |
| ledger.flag_merchant | 200 | 0.659 | 0.955 | 30.522 |
| **end to end** (decide_event wall) | 4500 | 2.666 | **5.612** | 127.665 |

**End-to-end P95 5.6 ms vs target < 20 ms: PASS.** Three other full runs on the same
laptop the same evening gave P95 5.1-7.4 ms (P50 2.5-3.0 ms).

Reading it:

- The engine proper (facts, rules, protections, warnings, decide, explain) is under 1 ms
  at P95 combined; the ledger (three SQLite round trips) is two thirds of the time.
  Inside the architecture.md budget: facts < 5 ms, rules + protections + signs < 5 ms,
  ledger transaction < 10 ms.
- The tail is not the engine: 33 of 4,500 decisions (0.7%) took ≥ 20 ms, and the
  outliers land wherever a pause hits, mostly the SQLite calls (commit on a fresh file)
  and Python's garbage collector. With `gc` disabled a 30-repetition run had 5/1,350 over
  20 ms instead of 18/1,350. Against the 2 s internal budget and the 8 s platform
  deadline this is noise.
- On Supabase each ledger round trip is a network hop (`select 1` median 51 ms from the
  laptop, database.md §5.2), so there the ledger, not the engine, sets the latency; this
  benchmark deliberately measures the engine on local SQLite.

## 2. Soft signal: Laya on the laptop

`ONEGUARD_SOFT_SIGNALS=laya`, `signals.warm()` once, then only the soft signal is timed,
10 repetitions: `soft_signals` per purchase (45, keywords + one Laya call per item line)
and the bare Laya `agent_directed` call per item line (56).

| stage | n | P50 ms | P95 ms | max ms |
|---|---:|---:|---:|---:|
| soft_signals per purchase (laya) | 450 | 48.0 | 99.4 | 151.4 |
| Laya agent_directed per item line | 560 | 41.6 | 68.7 | 120.8 |

- **Load (`signals.warm()`): 7.4 s** with the checkpoint cached (11.4 s and 21.4 s on the
  two runs before it; the first includes torch's first import after install). It happens
  once at API startup, never inside a decision.
- 0 of 450 purchases went over the pipeline's 500 ms soft-signal budget
  (`ONEGUARD_SIGNAL_BUDGET_MS`), so on this machine Laya never falls back to keywords for time.
- No purchase in the pack triggered on the model alone (0/450): with Laya on, the soft
  signal answers exactly as the keywords do on this pack.
- Laya warns at load that the checkpoint ships out-of-range temperatures and treats the
  affected confidences as uncalibrated; the 0.6 threshold (signals.py) is unchanged.

## 3. Soft signal: Laya in the image (CPU torch)

The deploy image (Dockerfile: CPU-only torch, `laya-typed-decisions` baked in), the same
`bench_engine.py --laya --reps 10`, run inside the container
(`docker exec <container> python scripts/bench_engine.py --laya`).

Local container, Docker Desktop on an Apple-silicon laptop (linux/aarch64, 6 CPUs), torch
CPU only:

| stage | n | P50 ms | P95 ms | max ms |
|---|---:|---:|---:|---:|
| soft_signals per purchase (laya) | 450 | 324.2 | 656.7 | 1357.5 |
| Laya agent_directed per item line | 560 | 285.4 | 395.8 | 711.1 |

- Load 13.5 s in the bench process; the app itself was answering `/healthz` with
  `model_loaded: true` 7 s after start. App RSS with the model: 2.1 GB.
- 115 of 450 purchases went over the 500 ms budget: a purchase asks once per item line, so
  multi-line carts fall back to keywords for time (never less cautious, signals.py).
- A second process with its own model copy takes another ~2.5 GB, so on the 4 GB machine the
  bench does not run beside the app: it runs on a throwaway machine of the same size and
  image (`fly machine run <image> python scripts/bench_engine.py --laya -a oneguard
  --vm-size shared-cpu-4x --vm-memory 4096 -r lhr --restart no --rm`).

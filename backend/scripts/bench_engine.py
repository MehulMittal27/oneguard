"""Engine latency benchmark: the 45 public purchases through the real pipeline (docs/benchmark.md).

    python scripts/bench_engine.py [--reps 100] [--warmup 3]
    ONEGUARD_SOFT_SIGNALS=laya python scripts/bench_engine.py --laya [--reps 10] [--lines-only]

Default mode: every scenario's events, each with its policy fixture
(``tests/fixtures/policies``), go through ``pipeline.decide_event`` with the registered
engine functions (a stubbed or missing one is an error) and a ``StoreLedger`` on a
fresh temp SQLite file per scenario per repetition, so every ``ledger.record`` inserts
(never a redelivery). History comes from a seeded temp SQLite, loaded once.
``ONEGUARD_DATABASE_URL`` is ignored: this never touches Supabase. Soft signals are off.
Each stage is timed with ``time.perf_counter`` around the call; end to end is the
``decide_event`` wall time, so it also covers what sits between the stages
(``add_ledger_results``, model copies, the API ``Decision``). Target: end-to-end P95 < 20 ms.

``--laya`` mode: loads Laya once with ``signals.warm()`` (reported as load time), then
times only the soft signal: ``soft_signals`` per purchase (45) and the model call per
item line (56), ``--reps`` times each. If Laya cannot load, the error is printed and
the rows are skipped. ``--lines-only`` times only the model call per item line: a short
run that leaves a shared-CPU machine's burst balance intact (docs/benchmark.md §3).
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
POLICIES = BACKEND / "tests" / "fixtures" / "policies"
TARGET_P95_MS = 20.0
LAYA_BUDGET_S = 60.0  # measure the model, not the pipeline's 500 ms cut-off
ENGINE_STAGES = ("build_facts", "evaluate_rules", "protections", "warning_signs", "decide", "explain")
LEDGER_STAGES = ("ledger.get", "ledger.view", "ledger.record", "ledger.flag_merchant")
DECIDING = frozenset({*ENGINE_STAGES, "soft_signals"})


def percentiles(samples: list[float]) -> tuple[float, float, float]:
    """(P50, P95, max) in the samples' unit."""
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return statistics.median(samples), cuts[94], max(samples)


def table(rows: list[tuple[str, list[float]]]) -> str:
    lines = ["| stage | n | P50 ms | P95 ms | max ms |", "|---|---:|---:|---:|---:|"]
    for name, samples in rows:
        p50, p95, top = percentiles(samples)
        lines.append(f"| {name} | {len(samples)} | {p50:.3f} | {p95:.3f} | {top:.3f} |")
    return "\n".join(lines)


def machine() -> str:
    def sysctl(key: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "?"

    cpu = sysctl("machdep.cpu.brand_string") if sys.platform == "darwin" else platform.processor()
    cores = os.cpu_count()
    ram = sysctl("hw.memsize") if sys.platform == "darwin" else "?"
    ram_gb = f"{int(ram) / 2**30:.0f} GB" if ram.isdigit() else "?"
    return f"{cpu}, {cores} cores, {ram_gb} RAM, {platform.platform()}, Python {platform.python_version()}"


def head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True, cwd=BACKEND
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def timed(fn: Callable[..., Any], sink: dict[str, float], name: str) -> Callable[..., Any]:
    """``fn`` that adds its wall time in ms to ``sink[name]``."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            sink[name] = sink.get(name, 0.0) + (time.perf_counter() - start) * 1000

    return wrapper


def bench_engine(reps: int, warmup: int) -> int:
    from sqlalchemy.orm import Session

    from oneguard.engine import stubs
    from oneguard.engine.interfaces import load_implementations
    from oneguard.engine.ledger import StoreLedger
    from oneguard.pipeline import PipelineContext, decide_event
    from oneguard.replay.events import Pack, build_events
    from oneguard.replay.runner import load_policy
    from oneguard.store import seed as seed_module
    from oneguard.store.db import init_db, make_engine, session
    from oneguard.store.history import StoreHistoryIndex

    implementations = load_implementations()
    missing = sorted(DECIDING - set(implementations))
    stubbed = sorted(DECIDING & stubs.STUBBED)
    if missing or stubbed:  # the real engine only: never a stand-in
        raise SystemExit(f"engine functions not real: missing {missing}, stubbed {stubbed}")

    pack = Pack.load()
    scenarios = pack.scenario_ids()
    policies = {s: load_policy(POLICIES / f"{s}.yaml", mandate_id=f"TM_BENCH_{s}") for s in scenarios}
    events = {s: build_events(pack, s, mandate_id=policies[s].mandate_id) for s in scenarios}
    total = sum(len(e) for e in events.values())

    samples: dict[str, list[float]] = {name: [] for name in (*ENGINE_STAGES, *LEDGER_STAGES, "end_to_end")}
    outcomes: dict[str, set[str]] = {}
    with tempfile.TemporaryDirectory(prefix="oneguard-bench-") as folder:
        root = Path(folder)
        reference = make_engine(f"sqlite:///{root / 'reference.sqlite'}")
        seed_module.run(engine=reference)
        with session(reference) as s:
            history = StoreHistoryIndex.load(s)
        reference.dispose()

        for rep in range(-warmup, reps):
            for scenario_id in scenarios:
                sink: dict[str, float] = {}
                engine = make_engine(f"sqlite:///{root / f'ledger-{rep}-{scenario_id}.sqlite'}")
                init_db(engine)
                with Session(engine, expire_on_commit=False) as s:
                    ledger = StoreLedger(s, history=history)
                    for method in LEDGER_STAGES:
                        attr = method.split(".", 1)[1]
                        setattr(ledger, attr, timed(getattr(ledger, attr), sink, method))
                    ctx = PipelineContext(
                        policy=policies[scenario_id], ledger=ledger, history=history,
                        run_id=f"bench-{rep}-{scenario_id}", signals_enabled=False,
                        implementations={
                            name: timed(fn, sink, name) if name in ENGINE_STAGES else fn
                            for name, fn in implementations.items()
                        },
                    )  # fmt: skip
                    for event in events[scenario_id]:
                        sink.clear()
                        start = time.perf_counter()
                        decision, _, _ = decide_event(event, ctx)
                        wall = (time.perf_counter() - start) * 1000
                        source = event["authorization"]["source_authorization_id"]
                        outcomes.setdefault(source, set()).add(decision.outcome)
                        if rep < 0:
                            continue
                        samples["end_to_end"].append(wall)
                        for name in (*ENGINE_STAGES, *LEDGER_STAGES):
                            if name in sink:
                                samples[name].append(sink[name])
                engine.dispose()

    unstable = sorted(source for source, seen in outcomes.items() if len(seen) > 1)
    if unstable:
        raise SystemExit(f"outcomes differ between repetitions: {unstable}")

    rows = [(name, samples[name]) for name in (*ENGINE_STAGES, *LEDGER_STAGES) if samples[name]]
    rows.append(("**end to end** (decide_event wall)", samples["end_to_end"]))
    p95 = percentiles(samples["end_to_end"])[1]
    verdict = "PASS" if p95 < TARGET_P95_MS else "FAIL"
    print(f"HEAD {head()} · {time.strftime('%Y-%m-%d %H:%M %Z')} · {machine()}")
    print(f"{total} events × {reps} reps (+{warmup} untimed warm-up), SQLite StoreLedger, signals off\n")
    print(table(rows))
    print(f"\nend-to-end P95 {p95:.3f} ms vs target < {TARGET_P95_MS:.0f} ms: {verdict}")
    over = sum(ms >= TARGET_P95_MS for ms in samples["end_to_end"])
    print(f"events at or over {TARGET_P95_MS:.0f} ms: {over}/{len(samples['end_to_end'])}")
    return 0 if verdict == "PASS" else 1


def bench_laya(reps: int, lines_only: bool = False) -> int:
    from oneguard.engine import signals
    from oneguard.engine.interfaces import load_implementations
    from oneguard.replay.events import Pack, build_events
    from oneguard.store import seed as seed_module
    from oneguard.store.db import make_engine, session
    from oneguard.store.history import StoreHistoryIndex

    if not isinstance(signals.BACKEND, signals.LayaSignals):
        raise SystemExit(f"--laya needs {signals.SIGNALS_ENV}=laya")
    build_facts = load_implementations()["build_facts"]

    pack = Pack.load()
    with tempfile.TemporaryDirectory(prefix="oneguard-bench-") as folder:
        reference = make_engine(f"sqlite:///{Path(folder) / 'reference.sqlite'}")
        seed_module.run(engine=reference)
        with session(reference) as s:
            history = StoreHistoryIndex.load(s)
        reference.dispose()
    facts = [build_facts(event, history) for s in pack.scenario_ids() for event in build_events(pack, s)]
    lines = [line.item_details for f in facts for line in f.items]

    print(f"HEAD {head()} · {time.strftime('%Y-%m-%d %H:%M %Z')} · {machine()}")
    start = time.perf_counter()
    loaded = signals.warm()
    load_ms = (time.perf_counter() - start) * 1000
    if not loaded:
        print(f"Laya did not load after {load_ms:.0f} ms (error above); laya rows skipped")
        return 1
    print(f"Laya load (signals.warm): {load_ms:.0f} ms\n")

    per_event: list[float] = []
    per_line: list[float] = []
    triggered = 0
    for _ in range(reps):
        for f in [] if lines_only else facts:
            start = time.perf_counter()
            [signal] = signals.soft_signals(f, LAYA_BUDGET_S)
            per_event.append((time.perf_counter() - start) * 1000)
            triggered += signal.triggered and signal.source == "model"
        for text in lines:
            start = time.perf_counter()
            signals.BACKEND.predict(text)
            per_line.append((time.perf_counter() - start) * 1000)
    print(f"{len(facts)} purchases / {len(lines)} item lines × {reps} reps\n")
    rows = [("soft_signals per purchase (laya)", per_event)] if per_event else []
    print(table([*rows, ("Laya agent_directed per item line", per_line)]))
    if lines_only:
        return 0
    over = sum(ms > signals_budget_ms() for ms in per_event)
    print(f"\npurchases over the pipeline's {signals_budget_ms():.0f} ms soft-signal budget: {over}/{len(per_event)}")
    print(f"model-only triggers: {triggered}/{len(per_event)}")
    return 0


def signals_budget_ms() -> float:
    from oneguard.pipeline import signal_budget_s_from_env

    return signal_budget_s_from_env() * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--laya", action="store_true", help="time the Laya soft signal instead of the engine")
    parser.add_argument("--lines-only", action="store_true", help="with --laya: only the model call per item line")
    parser.add_argument("--reps", type=int, help="repetitions (default 100, or 10 with --laya)")
    parser.add_argument("--warmup", type=int, default=3, help="untimed repetitions first (engine mode)")
    args = parser.parse_args()

    # Read at import by the engine modules, so set before the first oneguard import.
    os.environ.pop("ONEGUARD_DATABASE_URL", None)
    os.environ.pop("ONEGUARD_STUBS", None)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.laya:
        logging.getLogger("oneguard.engine.signals").setLevel(logging.INFO)  # which checkpoint loaded
        os.environ.setdefault("ONEGUARD_SOFT_SIGNALS", "laya")
        return bench_laya(args.reps or 10, args.lines_only)
    os.environ["ONEGUARD_SOFT_SIGNALS"] = "off"
    return bench_engine(args.reps or 100, args.warmup)


if __name__ == "__main__":
    raise SystemExit(main())

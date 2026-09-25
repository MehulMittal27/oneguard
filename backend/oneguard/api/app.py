"""The OneGuard app: ``/api`` (routes_customer, routes_dev), ``/healthz``, then the UI.

    uvicorn oneguard.api.app:app

The lifespan, in order (docs/architecture.md Runtime, docs/database.md §5):

1. ``init_db`` (creates missing tables, never drops or truncates); an empty store is
   seeded from the data pack, a seeded one is left as it is; form policies stored with
   their check texts as the instruction are set to "Built from the form"
   (``queries.restore_form_instructions``);
2. ``authorization_history`` loaded into memory (``StoreHistoryIndex``);
3. the database pool warmed, so the first decision does not pay a new connection;
4. the Viseca worker started, only when ``VISECA_API_KEY`` is set, in the background:
   it reads bootstrap and reference data, syncs the served reference tables into the
   store, then long-polls. Once started, the routes use its history index (reloaded if
   the sync changed anything), and C12 / D3 / D8 read the scenario bindings it stores
   (``scenario_profiles``) and the scenarios it serves. ``/healthz`` shows it;
5. the passport book opened (the signing key created on an empty store) and its sweep
   started in the background: every ``passport_sweep_s`` it signs the receipts of new
   decisions and re-signs answered step-ups; every ``PASSPORT_SYNC_EVERY`` sweeps (and
   first) it brings every mandate's passport up to date. The first sweep after a deploy
   is the backfill (docs/passport.md); it never delays the worker or a decision;
6. the soft-signal model (``ONEGUARD_SOFT_SIGNALS=laya``) loaded in the background, never
   before the worker polls: until it has loaded, and for good if it fails to load, the
   engine answers the signal with keywords (``signals.LayaSignals``). Loading Laya takes
   about 35 s on the cloud machine; a platform request in that window must not wait.

``/healthz`` reports the worker (state, last poll, events cursor), the machine when the
host exposes it (Fly: region, memory, CPUs), whether a model
provider is configured, the signals backend deciding now (``keywords`` while the model
loads), the configured one and whether its model is loading or loaded, the database
engine and a one-row round trip. It names no secret and no URL.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from oneguard import __version__
from oneguard.api import (
    errors,
    policies,
    queries,
    routes_customer,
    routes_dev,
    routes_passport,
    static,
)
from oneguard.api.models import _utc_z
from oneguard.api.offline import OfflineRunner
from oneguard.api.services import (
    COMPILE_TIMEOUT_S,
    DB_TIMEOUT_S,
    VISECA_TIMEOUT_S,
    Services,
    SignalsBackend,
)
from oneguard.engine import stubs
from oneguard.engine.types import HistoryIndex
from oneguard.llm.provider import (
    PROVIDER_ENV,
    Provider,
    get_provider,
    provider_available,
)
from oneguard.passport.book import PassportBook
from oneguard.store import seed as seed_module
from oneguard.store.db import get_engine, init_db, make_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.viseca.client import API_KEY_ENV, VisecaClient, call_sink, runs_allowed
from oneguard.viseca.worker import VisecaWorker

log = logging.getLogger(__name__)

SIGNALS_ENV = "ONEGUARD_SOFT_SIGNALS"
SIGNALS_BACKENDS: tuple[SignalsBackend, ...] = ("off", "keywords", "laya")
HEALTH_DB_TIMEOUT_S = 5.0
PASSPORT_SWEEP_S = 5.0
PASSPORT_SYNC_EVERY = 12
"""Passports are compared every 12th sweep (a minute); routes reissue them as they change."""
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_SECRETISH = re.compile(r"(?i)(password|passwd|pwd|token|key|secret)=\S+")


@dataclass
class AppConfig:
    """How to build the app. Defaults read the environment; tests inject the rest."""

    database_url: str | None = None
    viseca_client: Callable[[Engine], VisecaClient | None] | None = None
    """Builds the client; default: a real client when ``VISECA_API_KEY`` is set."""
    worker_options: dict[str, Any] = field(default_factory=dict)
    implementations: Mapping[str, Callable[..., Any]] | None = None
    stubbed: frozenset[str] | None = None
    provider: Provider | None = None
    signals_backend: str | None = None
    warm_signals: Callable[[SignalsBackend], bool] | None = None
    """Loads the soft-signal model in a background thread; default: ``signals.warm()``."""
    frontend_dist: Path | None = None
    db_timeout_s: float = DB_TIMEOUT_S
    viseca_timeout_s: float = VISECA_TIMEOUT_S
    compile_timeout_s: float = COMPILE_TIMEOUT_S
    passport_sweep_s: float | None = PASSPORT_SWEEP_S
    """Seconds between passport sweeps; None: no background sweep (tests call ``book.sync``)."""
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def signals_backend(value: str | None) -> SignalsBackend:
    """``ONEGUARD_SOFT_SIGNALS``; an unknown value runs the keyword detector (more cautious)."""
    chosen = (value if value is not None else os.environ.get(SIGNALS_ENV, "")).strip().lower() or "off"
    if chosen not in SIGNALS_BACKENDS:
        log.warning("%s=%r is not one of %s; using keywords", SIGNALS_ENV, chosen, "|".join(SIGNALS_BACKENDS))
        return "keywords"
    return chosen  # type: ignore[return-value]


def _default_client(db: Engine) -> VisecaClient | None:
    if not os.environ.get(API_KEY_ENV, "").strip():
        log.info("%s is not set: no Viseca worker (offline replay only)", API_KEY_ENV)
        return None
    return VisecaClient(sink=call_sink(db))


def _seed_if_empty(db: Engine) -> bool:
    with session(db) as s:
        if seed_module.history_row_count(s) > 0:
            return False
    log.warning("the store has no reference data; seeding it from the data pack")
    seed_module.run(engine=db)
    return True


def _load_history(db: Engine) -> StoreHistoryIndex:
    with session(db) as s:
        return StoreHistoryIndex.load(s)


def _warm_pool(db: Engine) -> int:
    """Open every pooled connection once (one ``SELECT 1`` each), then hand them back."""
    size = db.pool.size() if hasattr(db.pool, "size") else 1

    def open_one(_: int) -> Any:
        conn = db.connect()
        conn.execute(text("SELECT 1"))
        return conn

    with ThreadPoolExecutor(max_workers=size) as pool:
        conns = list(pool.map(open_one, range(size)))
    for conn in conns:
        conn.close()
    return len(conns)


def _warm_signals(backend: SignalsBackend) -> bool:
    """Warm the soft-signal detector if its module offers ``warm()``; True if a model loaded."""
    if backend == "off":
        return False
    try:
        module = importlib.import_module("oneguard.engine.signals")
    except ModuleNotFoundError:
        log.info("no soft-signal module yet; signals are the stub")
        return False
    warm = getattr(module, "warm", None)
    if not callable(warm):
        return False
    return bool(warm()) and backend == "laya"


def sanitise(message: str | None) -> str | None:
    """An error line safe for ``/healthz``: no URLs, no credentials."""
    if message is None:
        return None
    return _SECRETISH.sub(r"\1=[redacted]", _URL.sub("[url]", message))[:300]


async def _load_signals_model(s: Services, warm: Callable[[SignalsBackend], bool]) -> None:
    """Load the model off the event loop; the engine answers with keywords until it has.

    ``signals.LayaSignals`` switches itself the moment its model is set; this only keeps
    ``/healthz`` truthful. A failed load leaves keywords in charge and says so in the log.
    """
    s.model_loading = True
    started = time.perf_counter()
    try:
        s.model_loaded = await asyncio.to_thread(warm, s.signals_backend)
    except Exception:
        log.exception("the soft-signal model did not load; signals stay on keywords")
        s.model_loaded = False
    finally:
        s.model_loading = False
    if s.model_loaded:
        log.info("soft-signal model loaded in %.1f s; signals now use it", time.perf_counter() - started)
    else:
        log.warning("no soft-signal model loaded; signals stay on keywords")


async def _passport_sweep(s: Services, interval_s: float) -> None:
    """Keep passports and receipts signed (``PassportBook``); the first pass is the backfill."""
    assert s.book is not None
    book = s.book
    tick = 0
    while True:
        try:
            if tick % PASSPORT_SYNC_EVERY == 0:
                issued = await asyncio.to_thread(book.sync_passports)
                if tick == 0 or issued:
                    log.info("passports: %d version(s) issued", issued)
            counts = await asyncio.to_thread(book.sync_receipts)
            if tick == 0 or counts.receipts or counts.resolutions:
                log.info("receipts: %d issued, %d re-signed with an answer", counts.receipts, counts.resolutions)
            if tick == 0:
                log.info("passport book: %s", await asyncio.to_thread(book.counts))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("passport sweep failed; trying again in %.0f s", interval_s)
        tick += 1
        await asyncio.sleep(interval_s)


async def _start_worker(s: Services) -> None:
    assert s.worker is not None
    try:
        await s.worker.start()
    except Exception as exc:
        log.exception("the Viseca worker did not start")
        s.worker_error = f"worker did not start: {type(exc).__name__}"
        return
    s.history = s.worker.history


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: AppConfig = app.state.config
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # One INFO line per long-poll is noise; the client logs each call at DEBUG (viseca_calls only when switched on).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    own_engine = config.database_url is not None
    db = make_engine(config.database_url) if own_engine else get_engine()
    await asyncio.to_thread(init_db, db)
    await asyncio.to_thread(_seed_if_empty, db)
    if restored := await asyncio.to_thread(queries.restore_form_instructions, db, policies.FORM_INSTRUCTION):
        log.info("%d form mandate(s) now serve %r as their instruction", restored, policies.FORM_INSTRUCTION)
    started = time.perf_counter()
    history: HistoryIndex = await asyncio.to_thread(_load_history, db)
    log.info("history loaded in %.1f s", time.perf_counter() - started)
    warmed = await asyncio.to_thread(_warm_pool, db)
    log.info("database pool warmed: %d connection(s)", warmed)

    backend = signals_backend(config.signals_backend)
    provider = config.provider or get_provider()
    try:
        scenarios = routes_dev.scenario_bindings()
    except Exception:
        log.exception("could not read the scenario bindings")
        scenarios = {}

    s = Services(
        db_engine=db,
        history=history,
        provider=provider,
        provider_name=type(provider).__name__ if config.provider else os.environ.get(PROVIDER_ENV, "").strip() or "null",
        signals_backend=backend,
        offline=OfflineRunner(db, implementations=config.implementations, stubbed=config.stubbed, now=config.now),
        scenarios=scenarios,
        implementations=config.implementations,
        stubbed=config.stubbed,
        db_timeout_s=config.db_timeout_s,
        viseca_timeout_s=config.viseca_timeout_s,
        compile_timeout_s=config.compile_timeout_s,
        now=config.now,
    )
    app.state.services = s

    s.client = (config.viseca_client or _default_client)(db)
    start: asyncio.Task[None] | None = None
    if s.client is not None:
        models = s.live_models()
        options = {
            "implementations": config.implementations,
            "stubbed": config.stubbed,
            "now": config.now,
            **config.worker_options,
        }
        s.worker = VisecaWorker(
            s.client, db=db, provider=s.decision_provider(models), signals_enabled=models, **options
        )
        for mandate in await asyncio.to_thread(queries.mandates, db):
            s.bind_mandate(mandate)
        start = asyncio.create_task(_start_worker(s), name="viseca-worker-start")
    # After the worker's start is scheduled, so opening the book (the signing key, one
    # round trip) never delays the first poll (docs/decisions.md, startup gap).
    s.book = await asyncio.to_thread(PassportBook.open, db, config.now)
    load: asyncio.Task[None] | None = None
    if backend == "laya":
        load = asyncio.create_task(_load_signals_model(s, config.warm_signals or _warm_signals), name="signals-load")
    sweep: asyncio.Task[None] | None = None
    if config.passport_sweep_s is not None:
        sweep = asyncio.create_task(_passport_sweep(s, config.passport_sweep_s), name="passport-sweep")
    try:
        yield
    finally:
        for task in (start, load, sweep):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if s.worker is not None:
            await s.worker.stop()
        if s.client is not None:
            await s.client.aclose()
        await s.offline.stop()
        if own_engine:
            db.dispose()


def _database_engine(db: Engine) -> str:
    return db.url.get_backend_name()


async def healthz(request: Request) -> JSONResponse:
    s: Services = request.app.state.services
    db_ok, round_trip = True, None
    try:
        round_trip = await asyncio.wait_for(asyncio.to_thread(queries.round_trip_ms, s.db_engine), HEALTH_DB_TIMEOUT_S)
    except (TimeoutError, OSError, SQLAlchemyError) as exc:
        log.warning("health check round trip failed: %s", type(exc).__name__)
        db_ok = False

    worker: dict[str, Any] = {"configured": s.worker is not None}
    worker_ok = True
    if s.worker is not None:
        status = s.worker.status()
        worker_ok = status.ok
        worker.update(
            state=status.state,
            ok=status.ok,
            polling=status.state == "polling",
            last_poll_at=_utc_z(status.last_poll_at) if status.last_poll_at else None,
            events_cursor=status.events_cursor,
            human_window_s=status.human_window_s,
            decision_deadline_s=status.decision_deadline_s,
            pending_step_ups=status.pending_step_ups,
            history_reseeded=status.history_reseeded,
            fx_rates_match=status.fx_rates_match,
            fx_rates_mismatch=status.fx_rates_mismatch,
            runs=len(status.runs),
            last_error=sanitise(s.worker_error or status.last_error),
        )
    body = {
        "status": "ok" if db_ok and worker_ok else "degraded",
        "version": __version__,
        "worker": worker,
        "events_cursor": worker.get("events_cursor"),
        "runs_allowed": runs_allowed(),
        "provider": {"name": s.provider_name, "configured": provider_available(s.provider)},
        "signals": {
            "backend": s.active_signals(),
            "configured": s.signals_backend,
            "enabled": s.live_models(),
            "model_loading": s.model_loading,
            "model_loaded": s.model_loaded,
        },
        "model_loaded": s.model_loaded,
        "database": {"engine": _database_engine(s.db_engine), "ok": db_ok, "round_trip_ms": round_trip},
        "engine": {
            "stubbed": sorted(s.stubbed if s.stubbed is not None else (frozenset() if s.implementations else stubs.STUBBED))
        },
    }
    if (machine := machine_info()) is not None:
        body["machine"] = machine
    return JSONResponse(body, status_code=200 if db_ok else 503)


def machine_info() -> dict[str, Any] | None:
    """The machine the server runs on, when the host says (Fly sets ``FLY_MACHINE_ID``,
    ``FLY_REGION`` and ``FLY_VM_MEMORY_MB``); None elsewhere. Nothing secret."""
    machine_id = os.environ.get("FLY_MACHINE_ID", "").strip()
    if not machine_id:
        return None
    memory = os.environ.get("FLY_VM_MEMORY_MB", "").strip()
    return {
        "region": os.environ.get("FLY_REGION", "").strip() or None,
        "memory_mb": int(memory) if memory.isdigit() else None,
        "cpus": os.cpu_count(),
        "machine_id": machine_id,
    }


def create_app(config: AppConfig | None = None) -> FastAPI:
    app = FastAPI(title="OneGuard", version=__version__, lifespan=lifespan)
    app.state.config = config or AppConfig()
    errors.install(app)
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    app.include_router(routes_customer.router)
    app.include_router(routes_dev.router)
    app.include_router(routes_dev.catalogue_router)
    app.include_router(routes_passport.router)
    static.mount(app, app.state.config.frontend_dist)
    return app


app = create_app()

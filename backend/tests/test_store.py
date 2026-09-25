"""Store: schema, seed and HistoryIndex on a temporary SQLite database."""

from __future__ import annotations

import copy
import csv
import json
import logging
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError
from sqlalchemy.orm import Session

from oneguard.engine.types import HistoryIndex
from oneguard.store import seed as seed_module
from oneguard.store.db import database_url, init_db, make_engine, session
from oneguard.store.history import StoreHistoryIndex, normalise_merchant_name
from oneguard.store.schema import (
    REFERENCE_TABLES,
    AuthorizationHistory,
    Base,
    Card,
    Decision,
    Merchant,
    ScenarioCatalogue,
    WorkerState,
)
from tests.fake_viseca import JUDGING_EXTRA

DATA = Path(__file__).resolve().parents[2] / "data"


def pack_row_counts() -> dict[str, int]:
    """Rows per reference table, as ``data/metadata.json`` states them."""
    metadata = json.loads((DATA / "metadata.json").read_text(encoding="utf-8"))
    csv_rows = {e["path"]: e["rows"] for e in metadata["files"] if e.get("format") == "csv"}
    return {m.__tablename__: csv_rows[f"{m.__tablename__}.csv"] for m in REFERENCE_TABLES}


def stored_row_counts(engine: Engine) -> dict[str, int]:
    with session(engine) as s:
        return {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in REFERENCE_TABLES}


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    db = tmp_path_factory.mktemp("store") / "oneguard.sqlite"
    engine = make_engine(f"sqlite:///{db}")
    seed_module.run(engine=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def history(engine: Engine) -> StoreHistoryIndex:
    with session(engine) as s:
        return StoreHistoryIndex.load(s)


def test_seed_loads_every_reference_table(engine: Engine) -> None:
    with session(engine) as s:
        assert seed_module.history_row_count(s) == 4701
        assert s.scalar(select(func.count()).select_from(Merchant)) == 58


def test_every_reference_table_matches_the_pack(engine: Engine) -> None:
    expected = pack_row_counts()
    assert expected["authorization_history"] == 4701
    assert stored_row_counts(engine) == expected


def test_seed_is_idempotent(engine: Engine) -> None:
    expected = pack_row_counts()
    assert seed_module.run(engine=engine) == expected
    assert seed_module.run(engine=engine) == expected
    assert stored_row_counts(engine) == expected


def test_every_table_in_database_md_exists(engine: Engine) -> None:
    expected = {
        "customers", "accounts", "cards", "merchants", "items", "fx_rates",
        "authorization_history", "scenario_catalogue", "scenario_authorities",
        "policy_drafts", "mandates", "runs", "events_raw", "decisions",
        "merchant_flags", "requested_item_orders", "worker_state", "scenario_profiles", "viseca_calls",
    }  # fmt: skip
    assert set(Base.metadata.tables) == expected
    index_columns = {tuple(c.name for c in i.columns) for i in AuthorizationHistory.__table__.indexes}
    assert index_columns == {
        ("card_id", "timestamp"),
        ("customer_id", "merchant_id"),
        ("customer_id", "customer_device_id"),
        ("customer_id", "merchant_country"),
    }


def test_values_come_back_typed_and_in_utc(engine: Engine) -> None:
    with session(engine) as s:
        row = s.get(AuthorizationHistory, "TR00002")
        assert row.timestamp == datetime(2025, 9, 1, 6, 42, 40, tzinfo=UTC)
        assert row.billing_amount_chf == Decimal("22.95")
        assert row.recurring is True and row.card_present is False
        assert row.related_transaction_id is None and row.last_approved_at is None
        assert s.get(Merchant, "ME0001").name_normalised == "alpinebasket"


def test_runtime_tables_round_trip_json_and_utc(engine: Engine) -> None:
    decided = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    with session(engine) as s:
        s.add(
            Decision(
                live_authorization_id="live_1", run_id="run_1", mandate_id="mnd_1",
                card_id="CA0001", customer_id="CU0001", ts_sim=decided, outcome="step_up",
                final=False, uncertain_outcome="pending", reserved_chf=Decimal("44.50"),
                spent_chf=Decimal(0), merchant_id="ME0001", item_ids=["IT0001"],
                billing_amount_chf=Decimal("44.50"), related_live_id=None, relation=None,
                session_trust="normal", step=4, deciding_ids=["c7"], reason_codes=["unevaluable"],
                evidence=[{"rule": "Returns", "outcome": "uncertain", "detail": "not stated"}],
                message="m", counterfactual=None, explanation_source="template",
                injection_flag=None, engine_version="v", latency_ms=1.5, signals_enabled=False,
                decided_at=decided, deadline_at=decided, resolved_at=None, resolved_by=None,
            )
        )  # fmt: skip
    with session(engine) as s:
        stored = s.get(Decision, "live_1")
        assert stored.evidence[0]["outcome"] == "uncertain"
        assert stored.deadline_at == decided and stored.deadline_at.tzinfo is not None
        s.delete(stored)


def test_naive_datetimes_are_refused(engine: Engine) -> None:
    with pytest.raises(StatementError, match="naive datetime"), session(engine) as s:
        s.execute(select(AuthorizationHistory).where(AuthorizationHistory.timestamp > datetime(2026, 1, 1)))  # noqa: DTZ001


def test_history_index_for_cu0019(history: StoreHistoryIndex) -> None:
    assert isinstance(history, HistoryIndex)
    assert "ME0023" in history.known_merchants("CU0019")
    assert "ME0023" not in history.known_merchants_on_card("CA0039")
    assert history.known_merchants_on_card("CA0038")["ME0023"] == 2
    assert history.max_approved("CU0019") == 266.00
    assert "US" in history.known_countries("CU0019")


def test_history_index_other_answers(history: StoreHistoryIndex) -> None:
    attempts, approved = history.agent_history("CU0019")
    assert (attempts, approved) == (10, 9)
    assert isinstance(history.agent_history("CU0019"), tuple)
    assert history.agent_history("CU_NOBODY") == (0, 0)
    assert history.item_price_range("IT0001") == (12.00, 28.00, 90.00)
    assert history.item_price_range("IT_NOPE") is None
    assert history.max_approved("CU_NOBODY") is None
    assert history.known_merchants("CU_NOBODY") == {}
    assert history.merchant_names_normalised()["ME0001"] == "alpinebasket"
    assert history.last_price("CU0019", "ME0023") is not None
    assert history.known_devices("CU0019")


def test_recent_rows_window(history: StoreHistoryIndex) -> None:
    rows = history.recent_rows("CA0039", 90)
    assert rows and all(r.card_id == "CA0039" for r in rows)
    end = max(r.timestamp for r in rows)
    assert all((end - r.timestamp).days < 90 for r in rows)
    assert rows == sorted(rows, key=lambda r: (r.timestamp, r.authorization_id))
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    assert all(r.timestamp <= as_of for r in history.recent_rows("CA0039", 30, as_of=as_of))


def test_familiarity_counts_approved_purchases_only(history: StoreHistoryIndex) -> None:
    rows = [r for card in ("CA0038", "CA0039") for r in history.recent_rows(card, 10_000)]
    assert {r.transaction_type for r in rows} >= {"purchase"}
    approved = {r.merchant_id for r in rows if r.transaction_type == "purchase" and r.status == "approved"}
    assert set(history.known_merchants("CU0019")) == approved


def served_tables(extra: dict[str, list[dict]] | None = None) -> dict[str, list[dict]]:
    """``/v1/reference-data`` ``tables`` as the live sandbox serves them: the pack's rows as
    CSV strings (the catalogue with four columns and an int count), plus ``extra``."""
    tables = {}
    for name in ("customers", "accounts", "cards", "merchants", "items", "fx_rates", "scenario_catalogue"):
        with (DATA / f"{name}.csv").open(newline="", encoding="utf-8") as f:
            tables[name] = list(csv.DictReader(f))
    tables["scenario_catalogue"] = [
        {k: (int(v) if k == "event_count" else v) for k, v in row.items()
         if k in ("scenario_id", "scenario_name", "cardholder_instruction", "event_count")}
        for row in tables["scenario_catalogue"]
    ]  # fmt: skip
    for name, rows in (extra or {}).items():
        tables[name] = tables.get(name, []) + copy.deepcopy(rows)
    return tables


def test_served_tables_matching_the_store_change_nothing(engine: Engine) -> None:
    with Session(engine) as s:
        results = seed_module.sync_served(s, served_tables())
        assert [(r.table, r.changed, r.before, r.after) for r in results] == [
            (name, False, n, n)
            for name, n in pack_row_counts().items()
            if name not in ("authorization_history", "scenario_authorities")
        ]
        s.rollback()


def test_served_superset_is_upserted_once_and_nothing_is_deleted(engine: Engine) -> None:
    before = stored_row_counts(engine)
    served = served_tables(JUDGING_EXTRA)
    served["scenario_catalogue"] = [r for r in served["scenario_catalogue"] if r["scenario_id"] != "SCEN0004"]
    changed = next(m for m in served["merchants"] if m["merchant_id"] == "ME0002")
    changed["merchant_name"], changed["merchant_city"] = "Neighbour Pantry Plus", "Thun"
    with Session(engine) as s:
        first = {r.table: r for r in seed_module.sync_served(s, served)}
        assert {t: (r.inserted, r.updated) for t, r in first.items()} == {
            "customers": (1, 0), "accounts": (1, 0), "cards": (1, 0), "merchants": (1, 1),
            "items": (1, 0), "fx_rates": (0, 0), "scenario_catalogue": (1, 0),
        }  # fmt: skip
        assert first["customers"].after == before["customers"] + 1
        again = seed_module.sync_served(s, served)
        assert [r.table for r in again if r.changed] == []
        assert [r.served_sha256 for r in again] == [r.served_sha256 for r in first.values()]

        card = s.get(Card, "CA9001")
        assert (card.online_enabled, card.first_used_on.isoformat()) == (True, "2024-11-26")
        merchant = s.get(Merchant, "ME0002")
        assert (merchant.merchant_city, merchant.name_normalised) == ("Thun", "neighbourpantryplus")
        served_only = s.get(ScenarioCatalogue, "SCEN9001")
        assert (served_only.event_count, served_only.control_theme) == (1, "")
        kept = s.get(ScenarioCatalogue, "SCEN0004")  # not served: kept, with its own notes
        assert kept is not None and kept.control_theme
        s.rollback()
    assert stored_row_counts(engine) == before


def test_a_bad_served_table_leaves_the_store_as_it_was(engine: Engine) -> None:
    before = stored_row_counts(engine)
    orphan = copy.deepcopy(JUDGING_EXTRA["cards"][0]) | {"card_id": "CA9002", "account_id": "AC9999"}
    for served, error in (
        (served_tables({"cards": [orphan]}), IntegrityError),  # foreign key the store lacks
        (served_tables({"cards": [{**orphan, "online_enabled": "maybe"}]}), seed_module.PackMismatch),
        (served_tables({"customers": [{"customer_id": "CU9002"}]}), seed_module.PackMismatch),
    ):
        with Session(engine) as s, pytest.raises(error), s.begin():
            seed_module.sync_served(s, served)
    assert stored_row_counts(engine) == before


def test_pack_hash_mismatch_is_refused(tmp_path: Path) -> None:
    pack = tmp_path / "data"
    shutil.copytree(DATA, pack)
    with (pack / "fx_rates.csv").open("a", encoding="utf-8") as f:
        f.write("JPY,CHF,0.006000,2026-08-01,synthetic_fixed\n")
    with pytest.raises(seed_module.PackMismatch, match="fx_rates.csv"):
        seed_module.verify_pack(pack)


def test_init_db_adds_a_new_table_to_an_existing_store_and_keeps_its_rows(tmp_path: Path) -> None:
    """A table added to the schema reaches a live database (Supabase) without a reset."""
    engine = make_engine(f"sqlite:///{tmp_path / 'existing.sqlite'}")
    Base.metadata.create_all(engine, tables=[Merchant.__table__])
    with session(engine) as s:
        s.add(Merchant(
            merchant_id="M1", merchant_name="Shop", name_normalised="shop", merchant_category="c",
            merchant_mcc="5411", merchant_country="CH", merchant_city="Zurich",
            availability="online", recurring_capable=False,
        ))  # fmt: skip
    init_db(engine)
    init_db(engine)
    at = datetime(2026, 9, 24, tzinfo=UTC)
    with session(engine) as s:
        s.add(WorkerState(key="events_cursor", value=42, updated_at=at))
    with session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Merchant)) == 1
        assert s.get(WorkerState, "events_cursor").value == 42
    engine.dispose()


def test_reset_is_refused_in_prod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'prod.sqlite'}")
    init_db(engine)
    monkeypatch.setenv("ONEGUARD_ENV", "prod")
    with pytest.raises(seed_module.ResetRefused):
        seed_module.run(reset=True, engine=engine)
    assert seed_module.main(["--reset"]) == 1


def test_reset_drops_and_reseeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ONEGUARD_ENV", "dev")
    engine = make_engine(f"sqlite:///{tmp_path / 'dev.sqlite'}")
    assert seed_module.run(reset=True, engine=engine)["authorization_history"] == 4701
    assert seed_module.run(reset=True, engine=engine)["authorization_history"] == 4701


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "sqlite:///./oneguard.sqlite"),
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ],
)
def test_database_url(monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: str) -> None:
    if configured is None:
        monkeypatch.delenv("ONEGUARD_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("ONEGUARD_DATABASE_URL", configured)
    assert database_url() == expected


def test_postgres_engine_is_built_without_connecting() -> None:
    engine = make_engine("postgres://u:p@localhost:5432/db")
    assert engine.dialect.name == "postgresql" and engine.dialect.driver == "psycopg"
    assert engine.pool.size() == 5 and engine.pool._max_overflow == 0


def test_database_password_never_reaches_logs_or_reprs(caplog: pytest.LogCaptureFixture) -> None:
    canary = "pw-canary-7f3a9c"
    engine = make_engine(f"postgresql+psycopg://oneguard:{canary}@127.0.0.1:1/db?connect_timeout=2")
    caplog.set_level(logging.DEBUG, logger="sqlalchemy")  # sqlalchemy defaults itself to WARN
    with pytest.raises(OperationalError) as failed, engine.connect():
        pass
    engine.dispose()
    exposed = {
        "repr(engine)": repr(engine),
        "str(engine.url)": str(engine.url),
        "repr(engine.url)": repr(engine.url),
        "str(error)": str(failed.value),
        "repr(error)": repr(failed.value),
        "logs": caplog.text,
    }
    assert [where for where, text in exposed.items() if canary in text] == []


def test_normalise_merchant_name() -> None:
    assert normalise_merchant_name(" Pixel-Härbor 2 ") == "pixelharbor2"

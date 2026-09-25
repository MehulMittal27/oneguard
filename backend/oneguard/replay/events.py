"""Offline replay: turn the data pack's purchase attempts into live-shaped events, and a
live run's stored events into a replay from record (``recorded_events``).

Test tooling, not engine code. This is one of two places that may open the pack's CSVs
(the other is store/seed.py) and one of two places that may name scenario ids (the other
is api/routes_dev.py). Every event validates against
data/schemas/authorization_event.schema.json, so the pipeline sees the same shape offline
as it does from the Viseca long-poll.

What the platform would fill in at run time is filled in here:
- `recent_attempt_count_10m` is rebuilt from simulated timestamps (data_dictionary.md,
  "The two spend counters"); tests assert it equals the CSV column.
- `received_at` / `deadline_at` run on the real clock, as live.
- `context` starts empty; `with_run_context` fills it from the run's own decisions.
- Live ids: `live_id` maps a source id to the id the pipeline sees, and
  `related_authorization_id` is rewritten through the same map, as Viseca does.
"""

from __future__ import annotations

import copy
import csv
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

EVENT_SCHEMA = "schemas/authorization_event.schema.json"
DEADLINE_SECONDS = 8  # platform deadline from queueing (technical_details.md)
VELOCITY_WINDOW = timedelta(minutes=10)
HISTORY_WINDOW_MINUTES = 10
CONTEXT_BASIS = "run_decisions_and_scenario_timestamps"


def data_dir() -> Path:
    """The data pack: ONEGUARD_DATA_DIR, else <repo>/data."""
    override = os.environ.get("ONEGUARD_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(value: str) -> float:
    return float(value)


def _opt_num(value: str) -> float | None:
    return float(value) if value != "" else None


def _opt_str(value: str) -> str | None:
    return value if value != "" else None


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Pack:
    """The CSV rows replay needs, keyed by id."""

    attempts: list[dict[str, str]]
    items: dict[str, list[dict[str, str]]]
    merchants: dict[str, dict[str, str]]
    authorities: dict[str, dict[str, str]]
    scenarios: dict[str, dict[str, str]]

    @classmethod
    def load(cls, root: Path | None = None) -> Pack:
        root = root or data_dir()
        items: dict[str, list[dict[str, str]]] = {}
        for row in _read(root / "purchase_attempt_items.csv"):
            items.setdefault(row["authorization_id"], []).append(row)
        for lines in items.values():
            lines.sort(key=lambda r: int(r["line_no"]))
        return cls(
            attempts=_read(root / "purchase_attempts.csv"),
            items=items,
            merchants={r["merchant_id"]: r for r in _read(root / "merchants.csv")},
            authorities={r["authority_id"]: r for r in _read(root / "scenario_authorities.csv")},
            scenarios={r["scenario_id"]: r for r in _read(root / "scenario_catalogue.csv")},
        )

    def scenario_ids(self) -> list[str]:
        return sorted(self.scenarios)

    def attempts_for(self, scenario_id: str) -> list[dict[str, str]]:
        if scenario_id not in self.scenarios:
            raise KeyError(f"unknown scenario {scenario_id!r}")
        rows = [r for r in self.attempts if r["scenario_id"] == scenario_id]
        return sorted(rows, key=lambda r: int(r["replay_order"]))


def recent_attempt_counts(timestamps: list[datetime]) -> list[int]:
    """Earlier attempts in the same run with `t - 10 min <= ts < t`, current excluded.

    Counts every attempt regardless of outcome (data_dictionary.md). `timestamps` is in
    delivery order and simulated time is monotonic within a run.
    """
    return [
        sum(1 for earlier in timestamps[:i] if current - VELOCITY_WINDOW <= earlier < current)
        for i, current in enumerate(timestamps)
    ]


def _merchant(row: dict[str, str]) -> dict[str, Any]:
    return {
        "merchant_id": row["merchant_id"],
        "merchant_name": row["merchant_name"],
        "merchant_category": row["merchant_category"],
        "merchant_mcc": row["merchant_mcc"],
        "merchant_country": row["merchant_country"],
        "merchant_city": row["merchant_city"],
        "availability": row["availability"],
        "recurring_capable": row["recurring_capable"],
    }


def _item(row: dict[str, str]) -> dict[str, Any]:
    return {
        "line_no": int(row["line_no"]),
        "item_id": row["item_id"],
        "item_name": row["item_name"],
        "item_category": row["item_category"],
        "quantity": int(row["quantity"]),
        "unit_price": _num(row["unit_price"]),
        "currency": row["currency"],
        "item_details": row["item_details"],
    }


def build_events(
    pack: Pack,
    scenario_id: str,
    *,
    mandate_id: str = "TM_REPLAY",
    live_id: Callable[[str], str] = lambda source_id: source_id,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """One schema-valid `authorization.request` per attempt, in `replay_order`."""
    scenario = pack.scenarios[scenario_id]
    rows = pack.attempts_for(scenario_id)
    counts = recent_attempt_counts([parse_ts(r["timestamp"]) for r in rows])
    received = now or datetime.now(UTC)

    events = []
    for row, count in zip(rows, counts, strict=True):
        authority = pack.authorities[row["authority_id"]]
        source_id = row["authorization_id"]
        related = _opt_str(row["related_authorization_id"])
        authorization = {
            "authorization_id": live_id(source_id),
            "source_authorization_id": source_id,
            "scenario_id": row["scenario_id"],
            "replay_order": int(row["replay_order"]),
            "mandate_id": mandate_id,
            "profile_id": authority["authority_id"],
            "card_id": row["card_id"],
            "initiator_type": "agent",
            "merchant": _merchant(pack.merchants[row["merchant_id"]]),
            "timestamp": row["timestamp"],
            "amount": _num(row["amount"]),
            "currency": row["currency"],
            "billing_amount_chf": _num(row["billing_amount_chf"]),
            "items_subtotal": _num(row["items_subtotal"]),
            "delivery_fee": _num(row["delivery_fee"]),
            "channel": row["channel"],
            "customer_device_id": row["customer_device_id"],
            "authority_status": row["authority_status"],
            "card_status_at_attempt": row["card_status_at_attempt"],
            "spend_in_period_before_chf": _opt_num(row["spend_in_period_before_chf"]),
            "recent_attempt_count_10m": count,
            "fulfillment_method": row["fulfillment_method"],
            "delivery_by": _opt_str(row["delivery_by"]),
            "order_returnable": row["order_returnable"],
            "order_cancellable": row["order_cancellable"],
            "related_authorization_id": live_id(related) if related else None,
            "related_authorization_status": _opt_str(row["related_authorization_status"]),
            "purchase_description": row["purchase_description"],
            "items": [_item(line) for line in pack.items[source_id]],
        }
        events.append(
            {
                "type": "authorization.request",
                "request_id": f"req_{authorization['authorization_id']}",
                "deadline_at": format_ts(received + timedelta(seconds=DEADLINE_SECONDS)),
                "authorization": authorization,
                "mandate": {
                    "mandate_id": mandate_id,
                    "status": "active",
                    "customer_id": authority["customer_id"],
                    "card_id": authority["card_id"],
                    "instruction": scenario["cardholder_instruction"],
                    "hard_rules": [],
                    "uncertainty_policy": "ask",
                    "profile_id": authority["authority_id"],
                },
                "context": {"approved_spend_in_period_chf": None, "recent_authorizations": []},
                "runtime": {
                    "received_at": format_ts(received),
                    "history_window_minutes": HISTORY_WINDOW_MINUTES,
                    "context_basis": CONTEXT_BASIS,
                },
            }
        )
    return events


def recorded_events(
    stored: Iterable[dict[str, Any]], *, mandate_id: str, live_id: Callable[[str], str]
) -> list[dict[str, Any]]:
    """A live run's stored events (``events_raw``) as templates for a replay from record.

    The purchases are the platform's own, verbatim: amounts, items, shops, simulated
    timestamps and the platform's statements (``related_authorization_status``). What a
    new run changes is changed as in ``build_events``: ``live_id`` maps each stored live
    id to a fresh one, ``related_authorization_id`` is rewritten through the same map (an
    id from outside the run is kept), the mandate id is ``mandate_id`` and ``context``
    starts empty for ``with_run_context`` to fill from the replay's own decisions. The
    runner stamps the real-clock times at delivery.
    """
    events = []
    for event in stored:
        event = copy.deepcopy(event)
        auth = event["authorization"]
        auth["authorization_id"] = live_id(auth["authorization_id"])
        related = auth.get("related_authorization_id")
        if related:
            try:
                auth["related_authorization_id"] = live_id(related)
            except KeyError:
                pass  # an earlier run's purchase: kept as the platform named it
        auth["mandate_id"] = mandate_id
        event["mandate"] = {**event["mandate"], "mandate_id": mandate_id}
        event["request_id"] = f"req_{auth['authorization_id']}"
        event["context"] = {"approved_spend_in_period_chf": None, "recent_authorizations": []}
        events.append(event)
    return events


@dataclass(frozen=True)
class RunDecision:
    """One decision already taken in this run, as the platform would report it."""

    authorization_id: str  # live id
    timestamp: str  # simulated, ISO 8601 Z
    merchant_id: str
    billing_amount_chf: float
    status: str  # approved | declined | pending | cancelled


def with_run_context(event: dict[str, Any], decided: Iterable[RunDecision]) -> dict[str, Any]:
    """Return a copy of `event` whose `context` reflects the run's earlier decisions.

    Mirrors `context_basis = run_decisions_and_scenario_timestamps`: recent_authorizations
    are the decisions within `history_window_minutes` before this purchase's simulated
    time. `approved_spend_in_period_chf` stays null — the platform's period is not
    specified, and period tracking is the ledger's job (data_dictionary.md).
    """
    current = parse_ts(event["authorization"]["timestamp"])
    window = timedelta(minutes=event["runtime"]["history_window_minutes"])
    recent = [
        {
            "authorization_id": d.authorization_id,
            "timestamp": d.timestamp,
            "merchant_id": d.merchant_id,
            "billing_amount_chf": d.billing_amount_chf,
            "status": d.status,
        }
        for d in decided
        if current - window <= parse_ts(d.timestamp) < current
    ]
    return {**event, "context": {**event["context"], "recent_authorizations": recent}}


def event_validator(root: Path | None = None) -> Any:
    """A jsonschema validator for the live event contract, with format checking."""
    import json

    from jsonschema import Draft202012Validator

    schema = json.loads(((root or data_dir()) / EVENT_SCHEMA).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def all_events(pack: Pack | None = None, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
    """Every scenario's events, keyed by scenario id."""
    pack = pack or Pack.load()
    return {sid: build_events(pack, sid, **kwargs) for sid in pack.scenario_ids()}

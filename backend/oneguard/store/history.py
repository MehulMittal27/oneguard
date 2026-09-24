"""``HistoryIndex`` over the seeded store (docs/database.md §3).

``StoreHistoryIndex.load(session)`` reads ``authorization_history``, ``merchants`` and
``items`` once and answers every question from memory, so the decision path never
waits on a database round trip. Reference data is read-only at runtime; reload after
``make seed``.

Familiarity (known merchants, devices, countries, max_approved, last_price) counts
approved purchases only: refunds, cash withdrawals and declined attempts never make a
shop, device or country familiar (rules.md §3 Known shop, W1, W3, W4).
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from oneguard.engine.types import HistoryRow
from oneguard.store.schema import AuthorizationHistory, Item, Merchant

_EMPTY: Mapping[str, int] = MappingProxyType({})


def normalise_merchant_name(name: str) -> str:
    """Lower-case ASCII letters and digits only: "Pixel-Härbor " → "pixelharbor" (A7)."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in decomposed.casefold() if ch.isascii() and ch.isalnum())


def _row(r: AuthorizationHistory) -> HistoryRow:
    return HistoryRow(
        authorization_id=r.authorization_id,
        customer_id=r.customer_id,
        card_id=r.card_id,
        initiator_type=r.initiator_type,
        timestamp=r.timestamp,
        transaction_type=r.transaction_type,
        status=r.status,
        amount=float(r.amount),
        currency=r.currency,
        billing_amount_chf=float(r.billing_amount_chf),
        merchant_id=r.merchant_id,
        merchant_name=r.merchant_name,
        merchant_category=r.merchant_category,
        merchant_country=r.merchant_country,
        channel=r.channel,
        recurring=r.recurring,
        customer_device_id=r.customer_device_id or None,
        description=r.description,
    )


class StoreHistoryIndex:
    """In-memory implementation of ``engine.types.HistoryIndex``.

    ``merchant_names`` is merchant_id → catalogue name (``merchants.merchant_name``);
    the normalised names for A7 are derived from it with ``normalise_merchant_name``,
    the same function that fills ``merchants.name_normalised``.
    """

    def __init__(
        self,
        rows: Iterable[HistoryRow] = (),
        merchant_names: Mapping[str, str] | None = None,
        item_prices: Mapping[str, tuple[float, float, float]] | None = None,
    ) -> None:
        self._rows = sorted(rows, key=lambda r: (r.timestamp, r.authorization_id))
        self._names = dict(merchant_names or {})
        self._names_normalised = MappingProxyType(
            {m: normalise_merchant_name(name) for m, name in self._names.items()}
        )
        self._prices = dict(item_prices or {})
        self._end = self._rows[-1].timestamp if self._rows else None

        by_customer: dict[str, dict[str, int]] = defaultdict(dict)
        by_card: dict[str, dict[str, int]] = defaultdict(dict)
        devices: dict[str, set[str]] = defaultdict(set)
        countries: dict[str, set[str]] = defaultdict(set)
        max_approved: dict[str, float] = {}
        last_price: dict[tuple[str, str], float] = {}
        agent: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        rows_by_card: dict[str, list[HistoryRow]] = defaultdict(list)

        for r in self._rows:
            rows_by_card[r.card_id].append(r)
            if r.initiator_type == "agent":
                agent[r.customer_id][0] += 1
                agent[r.customer_id][1] += r.status == "approved"
            if r.transaction_type != "purchase" or r.status != "approved":
                continue
            merchants = by_customer[r.customer_id]
            merchants[r.merchant_id] = merchants.get(r.merchant_id, 0) + 1
            on_card = by_card[r.card_id]
            on_card[r.merchant_id] = on_card.get(r.merchant_id, 0) + 1
            if r.customer_device_id:
                devices[r.customer_id].add(r.customer_device_id)
            countries[r.customer_id].add(r.merchant_country)
            max_approved[r.customer_id] = max(
                max_approved.get(r.customer_id, r.billing_amount_chf), r.billing_amount_chf
            )
            last_price[(r.customer_id, r.merchant_id)] = r.billing_amount_chf

        self._by_customer = {k: MappingProxyType(v) for k, v in by_customer.items()}
        self._by_card = {k: MappingProxyType(v) for k, v in by_card.items()}
        self._devices = {k: frozenset(v) for k, v in devices.items()}
        self._countries = {k: frozenset(v) for k, v in countries.items()}
        self._max_approved = max_approved
        self._last_price = last_price
        self._agent = {k: (v[0], v[1]) for k, v in agent.items()}
        self._rows_by_card = dict(rows_by_card)

    @classmethod
    def load(cls, session: Session) -> StoreHistoryIndex:
        """Read history, merchant names and item prices from the store."""
        history = session.scalars(select(AuthorizationHistory)).all()
        names = session.execute(select(Merchant.merchant_id, Merchant.merchant_name)).all()
        items = session.scalars(select(Item)).all()
        return cls(
            rows=[_row(r) for r in history],
            merchant_names=dict(names),
            item_prices={
                i.item_id: (
                    float(i.unit_price_min_chf),
                    float(i.unit_price_typical_chf),
                    float(i.unit_price_max_chf),
                )
                for i in items
            },
        )

    def known_merchants(self, customer_id: str) -> Mapping[str, int]:
        return self._by_customer.get(customer_id, _EMPTY)

    def known_merchants_on_card(self, card_id: str) -> Mapping[str, int]:
        return self._by_card.get(card_id, _EMPTY)

    def known_devices(self, customer_id: str) -> frozenset[str]:
        return self._devices.get(customer_id, frozenset())

    def known_countries(self, customer_id: str) -> frozenset[str]:
        return self._countries.get(customer_id, frozenset())

    def max_approved(self, customer_id: str) -> float | None:
        return self._max_approved.get(customer_id)

    def last_price(self, customer_id: str, merchant_id: str) -> float | None:
        return self._last_price.get((customer_id, merchant_id))

    def recent_rows(
        self, card_id: str, days: int, as_of: datetime | None = None
    ) -> list[HistoryRow]:
        end = as_of or self._end
        if end is None:
            return []
        start = end - timedelta(days=days)
        return [r for r in self._rows_by_card.get(card_id, []) if start < r.timestamp <= end]

    def merchant_names_normalised(self) -> Mapping[str, str]:
        return self._names_normalised

    def merchant_names(self, merchant_ids: Iterable[str]) -> dict[str, str]:
        return {m: self._names[m] for m in merchant_ids if m in self._names}

    def agent_history(self, customer_id: str) -> tuple[int, int]:
        return self._agent.get(customer_id, (0, 0))

    def item_price_range(self, item_id: str) -> tuple[float, float, float] | None:
        return self._prices.get(item_id)

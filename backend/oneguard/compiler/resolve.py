"""Values the instruction points at but does not state (oracle ``value_from``).

"Same price as last time" is the customer's last approved price at the shop they mean,
read from HistoryIndex, never from text (A2). "By Friday" is a date resolved against the
card's simulated present (M6: the latest history row, not the real clock); the check
text shows the date so the customer confirms it (T5).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from oneguard.engine.types import HistoryIndex, HistoryRow

LOOKBACK_DAYS = 400  # a yearly renewal is still "last time"
_STOP = {"my", "the", "a", "an", "our", "same", "as", "last", "time", "renew", "again", "usual"}


def _tokens(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if t in _STOP or len(t) < 3:
            continue
        out.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return out


def card_rows(history: HistoryIndex, card_id: str, days: int = LOOKBACK_DAYS) -> list[HistoryRow]:
    if not card_id:
        return []
    return list(history.recent_rows(card_id, days))


def simulated_today(history: HistoryIndex, card_id: str) -> date | None:
    """The card's latest history timestamp as a date, or None without history."""
    rows = card_rows(history, card_id)
    return max(r.timestamp for r in rows).date() if rows else None


def last_price(
    history: HistoryIndex, card_id: str, item_words: str
) -> tuple[Decimal, HistoryRow] | None:
    """The last approved purchase on this card that matches ``item_words`` (by the
    trusted merchant category or the history description), priced by
    ``HistoryIndex.last_price`` for that shop. None when nothing matches."""
    wanted = _tokens(item_words)
    if not wanted:
        return None
    matches = [
        r for r in card_rows(history, card_id)
        if r.transaction_type == "purchase" and r.status == "approved"
        and wanted & _tokens(f"{r.description} {r.merchant_category} {r.merchant_name}")
    ]
    if not matches:
        return None
    row = max(matches, key=lambda r: (r.timestamp, r.authorization_id))
    price = history.last_price(row.customer_id, row.merchant_id)
    return Decimal(str(price if price is not None else row.billing_amount_chf)), row

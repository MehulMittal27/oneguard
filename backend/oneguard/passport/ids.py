"""Stable ids for passports and receipts (docs/passport.md).

Both are derived from the id they document, so every process names the same document
the same way: a passport is one per mandate, a receipt one per live authorization id.
"""

from __future__ import annotations

import hashlib


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def passport_id_for(mandate_id: str) -> str:
    """The passport of this mandate, the same across its versions."""
    return f"pp_{_digest(mandate_id)}"


def receipt_id_for(live_authorization_id: str) -> str:
    """The receipt of the decision on this live authorization id."""
    return f"rc_{_digest(live_authorization_id)}"

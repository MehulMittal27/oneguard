"""The passport book: issues passports and receipts from what the store holds (docs/passport.md).

Everything here is a reconciliation, so it is idempotent and safe to run anywhere, any
number of times, by more than one process:

- ``sync_passports``: every active mandate has a passport whose latest version says what
  the mandate and the card's enrolled devices say now; a revoked mandate's passport gets a
  last version with ``revoked_at``. A version is issued only when the content changed.
- ``sync_receipts``: every decision has a receipt; a step-up answered since its receipt was
  signed is re-signed with the resolution (the earlier signature kept in ``history``).

Routes call these right after a change (C2, C4, C5, C8, devices); the app's sweep runs them
in the background, which is also how the first start after the deploy backfills every
active mandate and every earlier decision. Nothing here ever runs on a decision's path to
Viseca, and nothing ever deletes a row. Ids: ``ids.passport_id_for`` / ``receipt_id_for``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from oneguard.api import policies
from oneguard.api.queries import entry_of
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.passport.canonical import timestamp
from oneguard.passport.documents import (
    ISSUER,
    PASSPORT_TYPE,
    RECEIPT_TYPE,
    passport_body,
    passport_content,
    receipt_body,
    resolution,
)
from oneguard.passport.ids import passport_id_for, receipt_id_for
from oneguard.passport.keys import KeyRing
from oneguard.store.db import session
from oneguard.store.schema import (
    Customer,
    Decision,
    Device,
    EventRaw,
    Mandate,
    Passport,
    Receipt,
)

log = logging.getLogger(__name__)

RECEIPT_BATCH = 200
"""Receipts issued per sync pass; the sweep's next pass takes the rest (the backfill)."""


@dataclass(frozen=True)
class SyncCounts:
    passports: int = 0
    receipts: int = 0
    resolutions: int = 0


class PassportBook:
    """Issues, re-signs and verifies passports and receipts over one store."""

    def __init__(self, db: Engine, keys: KeyRing, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.db = db
        self.keys = keys
        self.now = now
        self._lock = threading.Lock()
        self._answers: dict[str, str] = {}
        """live id → the device that answered it (C8), until its receipt carries it."""

    @classmethod
    def open(cls, db: Engine, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> PassportBook:
        return cls(db, KeyRing.load_or_create(db, now), now)

    # Passports ---------------------------------------------------------------------------

    def sync_passports(self, *, card_id: str | None = None, first_reason: str = "backfill") -> int:
        """Bring every mandate's passport (on ``card_id``, or everywhere) up to date.

        An active mandate with no passport gets version 1 (``first_reason``: ``confirmed``
        when C2 calls, ``backfill`` from the sweep). Returns the versions issued.
        """
        with self._lock:
            with session(self.db) as s:
                query = select(Mandate)
                if card_id is not None:
                    query = query.where(Mandate.card_id == card_id)
                mandates = list(s.scalars(query.order_by(Mandate.confirmed_at, Mandate.mandate_id)))
            issued = 0
            for mandate in mandates:
                try:
                    issued += self._sync_passport(mandate, first_reason)
                except IntegrityError:  # another process issued the same version first
                    log.info("passport of %s was issued elsewhere; next pass compares again", mandate.mandate_id)
            return issued

    def _sync_passport(self, mandate: Mandate, first_reason: str) -> int:
        passport_id = mandate.passport_id or passport_id_for(mandate.mandate_id)
        with session(self.db) as s:
            latest = self._latest(s, passport_id)
            if latest is None and mandate.status != "active":
                return 0  # a policy revoked before passports existed has none
            if latest is not None and latest.revoked_at is not None:
                return 0  # a revoked passport is final: nothing it says changes again
            holder = s.get(Customer, mandate.customer_id)
            enrolled = [
                {"device_id": d.device_id, "label": d.label, "enrolled_at": d.enrolled_at,
                 "enrolled_by_device_id": d.enrolled_by_device_id}
                for d in s.scalars(
                    select(Device)
                    .where(Device.card_id == mandate.card_id, Device.status == "enrolled")
                    .order_by(Device.enrolled_at, Device.device_id)
                )
            ]  # fmt: skip
            confirmations = s.scalar(
                select(func.count())
                .select_from(Decision)
                .where(
                    Decision.mandate_id == mandate.mandate_id,
                    Decision.outcome == "step_up",
                    Decision.uncertain_outcome == "approved",
                    Decision.resolved_by == "customer",
                )
            ) or 0
            rules, flags = policies.load_rules(mandate.rules, mandate.checks)
            now = self.now()
            revoked_at = (mandate.revoked_at or now) if mandate.status != "active" else None
            version = (latest.version + 1) if latest is not None else 1
            body = passport_body(
                passport_id=passport_id, version=version, customer_id=mandate.customer_id,
                holder_name=holder.persona_name if holder else mandate.customer_id, card_id=mandate.card_id,
                mandate_id=mandate.mandate_id, instruction=mandate.instruction, rules=rules, flags=flags,
                uncertainty_policy=mandate.uncertainty_policy, remembered_confirmations=int(confirmations),
                devices=enrolled, issued_at=now, revoked_at=revoked_at, key_id=self.keys.active_key_id,
            )  # fmt: skip
            if latest is not None and passport_content(latest.document) == passport_content(body):
                return 0
            reason = first_reason if latest is None else _reason(latest.document, body)
            signature, key_id = self.keys.sign(body)
            if latest is not None:
                latest.superseded_at = now
            s.add(
                Passport(
                    passport_id=passport_id, version=version, mandate_id=mandate.mandate_id,
                    card_id=mandate.card_id, customer_id=mandate.customer_id, document=body,
                    signature=signature, key_id=key_id, issued_at=now, reason=reason,
                    superseded_at=None, revoked_at=revoked_at,
                )
            )  # fmt: skip
            if mandate.passport_id != passport_id:
                s.execute(update(Mandate).where(Mandate.mandate_id == mandate.mandate_id).values(passport_id=passport_id))
        log.info("passport %s v%d issued (%s)", passport_id, version, reason)
        return 1

    @staticmethod
    def _latest(s: Session, passport_id: str) -> Passport | None:
        return s.scalar(
            select(Passport).where(Passport.passport_id == passport_id).order_by(Passport.version.desc()).limit(1)
        )

    def card_passport(self, card_id: str) -> tuple[Passport, list[Passport]] | None:
        """The passport of the card's current mandate (active, else the latest) and every
        version of it, oldest first."""
        from oneguard.api.queries import latest_mandate

        mandate = latest_mandate(self.db, card_id)
        if mandate is None:
            return None
        return self.passport(mandate.passport_id or passport_id_for(mandate.mandate_id))

    def passport(self, passport_id: str) -> tuple[Passport, list[Passport]] | None:
        with session(self.db) as s:
            versions = list(
                s.scalars(select(Passport).where(Passport.passport_id == passport_id).order_by(Passport.version))
            )
        return (versions[-1], versions) if versions else None

    def passport_version(self, passport_id: str, version: int) -> Passport | None:
        with session(self.db) as s:
            return s.get(Passport, (passport_id, version))

    def summaries(self, mandate_ids: Iterable[str]) -> dict[str, Passport]:
        """mandate id → its passport's latest version, for the mandates that have one."""
        ids = sorted(set(mandate_ids))
        if not ids:
            return {}
        with session(self.db) as s:
            rows = list(s.scalars(select(Passport).where(Passport.mandate_id.in_(ids))))
        latest: dict[str, Passport] = {}
        for row in rows:
            if row.mandate_id not in latest or row.version > latest[row.mandate_id].version:
                latest[row.mandate_id] = row
        return latest

    # Receipts ----------------------------------------------------------------------------

    def note_answer(self, live_id: str, device_id: str) -> None:
        """C8: the device that answered this step-up, for its receipt's resolution."""
        with self._lock:
            self._answers[live_id] = device_id

    def sync_receipts(self, live_ids: Iterable[str] | None = None, *, limit: int = RECEIPT_BATCH) -> SyncCounts:
        """Issue the missing receipts and re-sign the ones whose step-up was answered since.

        ``live_ids`` limits the pass to those decisions (C8 after an answer); otherwise up
        to ``limit`` of each kind, oldest decision first.
        """
        wanted = sorted(set(live_ids)) if live_ids is not None else None
        with self._lock:
            with session(self.db) as s:
                missing = select(Decision).outerjoin(
                    Receipt, Receipt.live_authorization_id == Decision.live_authorization_id
                ).where(Receipt.receipt_id.is_(None))
                answered = select(Decision).join(
                    Receipt, Receipt.live_authorization_id == Decision.live_authorization_id
                ).where(Decision.outcome == "step_up", Decision.final.is_(True), Receipt.resolved_at.is_(None))
                if wanted is not None:
                    missing = missing.where(Decision.live_authorization_id.in_(wanted))
                    answered = answered.where(Decision.live_authorization_id.in_(wanted))
                new = [entry_of(r) for r in s.scalars(missing.order_by(Decision.decided_at).limit(limit))]
                due = [entry_of(r) for r in s.scalars(answered.order_by(Decision.decided_at).limit(limit))]
            issued = sum(self._issue_receipt(e) for e in new)
            resolved = sum(self._resolve_receipt(e) for e in due)
        return SyncCounts(receipts=issued, resolutions=resolved)

    def _checks_and_passport(self, s: Session, entry: LedgerEntry) -> tuple[tuple[str, int] | None, list[dict[str, Any]]]:
        """The passport version in force when the decision was made (the first one for a
        decision older than every version: the backfill), and the checks it lists."""
        versions = list(
            s.scalars(select(Passport).where(Passport.mandate_id == entry.mandate_id).order_by(Passport.version))
        )
        if versions:
            before = [v for v in versions if v.issued_at <= entry.decided_at]
            chosen = before[-1] if before else versions[0]
            return (chosen.passport_id, chosen.version), list(chosen.document.get("checks") or [])
        mandate = s.get(Mandate, entry.mandate_id)
        return None, list(mandate.checks) if mandate is not None else []

    def _issue_receipt(self, entry: LedgerEntry) -> int:
        receipt_id = entry.receipt_id or receipt_id_for(entry.live_authorization_id)
        device_id = self._answers.get(entry.live_authorization_id)
        try:
            with session(self.db) as s:
                raw = s.get(EventRaw, entry.live_authorization_id)
                passport, checks = self._checks_and_passport(s, entry)
                body = receipt_body(
                    entry, raw.event if raw else None, raw.source_authorization_id if raw else None,
                    passport, checks, device_id, self.keys.active_key_id,
                )  # fmt: skip
                signature, key_id = self.keys.sign(body)
                resolved = body["resolution"]
                s.add(
                    Receipt(
                        receipt_id=receipt_id, live_authorization_id=entry.live_authorization_id,
                        passport_id=passport[0] if passport else None,
                        passport_version=passport[1] if passport else None,
                        document=body, signature=signature, key_id=key_id, issued_at=self.now(),
                        answered_by_device_id=resolved["device_id"] if resolved else None,
                        resolved_at=(entry.resolved_at or self.now()) if resolved else None, history=[],
                    )
                )  # fmt: skip
                if entry.receipt_id is None:  # a decision stored before receipts existed
                    s.execute(
                        update(Decision)
                        .where(Decision.live_authorization_id == entry.live_authorization_id)
                        .values(receipt_id=receipt_id)
                    )
        except IntegrityError:
            return 0  # issued by another process
        if resolved:
            self._answers.pop(entry.live_authorization_id, None)
        return 1

    def _resolve_receipt(self, entry: LedgerEntry) -> int:
        device_id = self._answers.get(entry.live_authorization_id)
        answer = resolution(entry, device_id)
        if answer is None:
            return 0
        with session(self.db) as s:
            row = s.scalar(select(Receipt).where(Receipt.live_authorization_id == entry.live_authorization_id))
            if row is None or row.resolved_at is not None:
                return 0
            earlier = {"document": row.document, "signature": row.signature, "key_id": row.key_id,
                       "signed_at": timestamp(row.issued_at)}  # fmt: skip
            body = {**row.document, "resolution": answer, "issuer": {"name": ISSUER, "key_id": self.keys.active_key_id}}
            signature, key_id = self.keys.sign(body)
            changed = s.execute(
                update(Receipt)
                .where(Receipt.receipt_id == row.receipt_id, Receipt.resolved_at.is_(None))
                .values(
                    document=body, signature=signature, key_id=key_id, history=[*row.history, earlier],
                    answered_by_device_id=answer["device_id"], resolved_at=entry.resolved_at or self.now(),
                )
            ).rowcount  # fmt: skip
        self._answers.pop(entry.live_authorization_id, None)
        return 1 if changed else 0

    def receipt(self, live_id: str) -> Receipt | None:
        """The decision's receipt, issued now if the sweep has not reached it yet."""
        with session(self.db) as s:
            row = s.scalar(select(Receipt).where(Receipt.live_authorization_id == live_id))
            known = row is not None or s.get(Decision, live_id) is not None
        if row is None and known:
            self.sync_receipts([live_id])
            with session(self.db) as s:
                row = s.scalar(select(Receipt).where(Receipt.live_authorization_id == live_id))
        return row

    def receipt_by_id(self, receipt_id: str) -> Receipt | None:
        with session(self.db) as s:
            return s.get(Receipt, receipt_id)

    # Everything --------------------------------------------------------------------------

    def sync(self) -> SyncCounts:
        """One sweep: passports, then receipts (a receipt names the passport in force)."""
        passports = self.sync_passports()
        counts = self.sync_receipts()
        return SyncCounts(passports=passports, receipts=counts.receipts, resolutions=counts.resolutions)

    def counts(self) -> dict[str, int]:
        """How many of each document the store holds (the post-deploy backfill report)."""
        with session(self.db) as s:
            return {
                "active_mandates": s.scalar(select(func.count()).select_from(Mandate).where(Mandate.status == "active")) or 0,
                "passports": s.scalar(select(func.count(func.distinct(Passport.passport_id)))) or 0,
                "passport_versions": s.scalar(select(func.count()).select_from(Passport)) or 0,
                "decisions": s.scalar(select(func.count()).select_from(Decision)) or 0,
                "receipts": s.scalar(select(func.count()).select_from(Receipt)) or 0,
                "devices_enrolled": s.scalar(
                    select(func.count()).select_from(Device).where(Device.status == "enrolled")
                ) or 0,
            }  # fmt: skip

    # Verify ------------------------------------------------------------------------------

    def verify(self, document: Any, signature: Any, key_id: Any) -> tuple[bool, str]:
        """Whether ``signature`` is OneGuard's signature of ``document`` under ``key_id``."""
        if not isinstance(document, dict) or not isinstance(signature, str) or not isinstance(key_id, str):
            return False, "A document, a signature and a key id are needed."
        if document.get("type") not in (PASSPORT_TYPE, RECEIPT_TYPE):
            return False, "Not a OneGuard passport or receipt."
        issuer = document.get("issuer") if isinstance(document.get("issuer"), dict) else {}
        if issuer.get("key_id") != key_id:
            return False, "The document names another signing key than the one given."
        if not self.keys.verify(document, signature, key_id):
            if not self.keys.knows(key_id):
                return False, f"Key {key_id} is not a OneGuard key."
            return False, "The signature does not match the document: it was changed after signing."
        return True, f"Signed by OneGuard key {key_id}."


def _reason(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Why a new passport version: the first difference that matters to the holder."""
    if after.get("revoked_at") and not before.get("revoked_at"):
        return "revoked"
    if before.get("checks") != after.get("checks") or before.get("uncertainty_policy") != after.get("uncertainty_policy"):
        return "tightened"
    if before.get("devices") != after.get("devices"):
        return "devices"
    if before.get("remembered_confirmations") != after.get("remembered_confirmations"):
        return "confirmation"
    return "updated"



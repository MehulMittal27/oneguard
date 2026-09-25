"""The two signed documents (docs/passport.md, "Documents"): pure builders, no store.

Passport: one per mandate, a new version whenever what it says changes. Receipt: one per
decision, re-signed once when a step-up is answered. Both carry money as two-decimal
strings and timestamps as ISO 8601 UTC with ``Z`` (``canonical``), so the stored document
is byte for byte what was signed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from oneguard.api.policies import NOTHING_EXTRA_CHECK, REQUESTED_ITEM_CHECK
from oneguard.engine.explain import RULE_LABELS
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import Rule
from oneguard.passport.canonical import money, normalise, sha256_hex, timestamp
from oneguard.passport.ids import receipt_id_for

PASSPORT_TYPE = "oneguard.passport/1"
RECEIPT_TYPE = "oneguard.receipt/1"
ISSUER = "OneGuard"
PASSPORT_LIFETIME = timedelta(days=90)

_CHECK_KEYS = ("id", "text", "source", "field", "operator", "value", "currency", "scope", "period_days", "on_fail")
_OPTIONAL_CHECK_KEYS = ("currency", "scope", "period_days")
_PERMITTED = {"pass": "pass", "fail": "fail", "uncertain": "unknown", "info": "info"}
_FLAG_CHECKS = {REQUESTED_ITEM_CHECK: ("requested_item", "C5"), NOTHING_EXTRA_CHECK: ("nothing_extra", "C10")}
"""A check that shows a policy flag: (the flag, the engine's rule id for its result)."""
PASSPORT_CONTENT_EXCLUDED = ("version", "issued_at", "expires_at", "issuer")
"""What a new version always changes; the rest is compared to decide whether one is due."""


def check(rule: Rule) -> dict[str, Any]:
    """One confirmed rule as the passport lists it (the optional keys only when set)."""
    data = rule.model_dump(mode="json")
    out = {key: data.get(key) for key in _CHECK_KEYS}
    for key in _OPTIONAL_CHECK_KEYS:
        if out[key] is None:
            del out[key]
    return out


def passport_checks(confirmed: Iterable[Mapping[str, Any]], rules: Sequence[Rule], flags: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every check the customer confirmed, in their order: a typed rule's field, operator and
    value, or, for a check that shows a policy flag (the requested item, nothing extra), the
    flag and its value with no field of its own."""
    by_id = {r.id: r for r in rules}
    out: list[dict[str, Any]] = []
    for c in confirmed:
        if c["id"] in by_id:
            out.append(check(by_id[c["id"]]))
        elif c["id"] in _FLAG_CHECKS:
            flag = _FLAG_CHECKS[c["id"]][0]
            out.append({"id": c["id"], "text": c.get("text"), "source": c.get("source"), "field": None,
                        "operator": None, "value": flags.get(flag), "flag": flag, "on_fail": "decline"})  # fmt: skip
    return out


def passport_body(
    *,
    passport_id: str,
    version: int,
    customer_id: str,
    holder_name: str,
    card_id: str,
    mandate_id: str,
    instruction: str,
    checks: Sequence[Mapping[str, Any]],
    flags: Mapping[str, Any],
    uncertainty_policy: str,
    remembered_confirmations: int,
    devices: Iterable[Mapping[str, Any]],
    issued_at: datetime,
    revoked_at: datetime | None,
    key_id: str,
) -> dict[str, Any]:
    """The passport's signed body. ``flags`` keeps only the policy flags that restrict
    something (``requested_item``, ``nothing_extra``, ...); ``devices`` are the card's
    enrolled devices."""
    return normalise({
        "type": PASSPORT_TYPE,
        "passport_id": passport_id,
        "version": version,
        "holder": {"customer_id": customer_id, "name": holder_name},
        "card_id": card_id,
        "mandate_id": mandate_id,
        "instruction": instruction,
        "checks": list(checks),
        "flags": {k: v for k, v in sorted(flags.items()) if v not in (None, False, [], "")},
        "uncertainty_policy": uncertainty_policy,
        "remembered_confirmations": remembered_confirmations,
        "devices": [
            {
                "device_id": d["device_id"],
                "label": d["label"],
                "enrolled_at": d["enrolled_at"],
                "enrolled_by_device_id": d["enrolled_by_device_id"],
            }
            for d in devices
        ],
        "issued_at": issued_at,
        "expires_at": issued_at + PASSPORT_LIFETIME,
        "revoked_at": revoked_at,
        "issuer": {"name": ISSUER, "key_id": key_id},
    })  # fmt: skip


def passport_content(document: Mapping[str, Any]) -> dict[str, Any]:
    """What a passport says, without what every version changes."""
    return {k: v for k, v in document.items() if k not in PASSPORT_CONTENT_EXCLUDED}


def permitted(evidence: Iterable[Mapping[str, Any]], checks: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Each check the decision evaluated, with its outcome, from the decision's evidence.

    An evidence row names a check by its text (a typed rule), or by the engine's label for
    a policy flag or step-1 check (``explain.RULE_LABELS``); signal rows are not checks.
    A check with no row was not evaluated and is left out (missing is never a pass).
    """
    by_label: dict[str, str] = {}
    for c in checks:
        by_label.setdefault(str(c.get("text") or c["id"]), str(c["id"]))
        by_label.setdefault(str(c["id"]), str(c["id"]))
        if c["id"] in _FLAG_CHECKS:  # the flag's result is labelled by the engine's rule id
            by_label.setdefault(RULE_LABELS[_FLAG_CHECKS[c["id"]][1]], str(c["id"]))
    for check_id, label in RULE_LABELS.items():
        by_label.setdefault(label, check_id)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in evidence:
        check_id = by_label.get(str(row.get("rule")))
        if check_id is None or check_id in seen:
            continue
        seen.add(check_id)
        out.append({"check_id": check_id, "outcome": _PERMITTED.get(str(row.get("outcome")), "info")})
    return out


def resolution(entry: LedgerEntry, device_id: str | None) -> dict[str, Any] | None:
    """A step-up's answer: the customer's (with the device that gave it) or the timeout's."""
    if entry.outcome != "step_up" or not entry.final or entry.uncertain_outcome in (None, "pending"):
        return None
    by = entry.resolved_by or ("timeout" if entry.uncertain_outcome == "expired" else "customer")
    at = entry.resolved_at or entry.decided_at
    return normalise({
        "outcome": "approve" if entry.uncertain_outcome == "approved" else "decline",
        "resolved_by": by,
        "device_id": device_id if by == "customer" else None,
        "resolved_at": at,
    })  # fmt: skip


def receipt_body(
    entry: LedgerEntry,
    event: Mapping[str, Any] | None,
    source_id: str | None,
    passport: tuple[str, int] | None,
    checks: Iterable[Mapping[str, Any]],
    device_id: str | None,
    key_id: str,
) -> dict[str, Any]:
    """The receipt's signed body for a stored decision and the event it decided.

    With no stored event (a decision recorded without one), the amount, currency and
    items hash are ``null`` rather than guessed.
    """
    auth = (event or {}).get("authorization") or {}
    items = auth.get("items")
    evidence = [row.model_dump(mode="json") for row in entry.evidence]
    return normalise({
        "type": RECEIPT_TYPE,
        "receipt_id": entry.receipt_id or receipt_id_for(entry.live_authorization_id),
        "passport_id": passport[0] if passport else None,
        "passport_version": passport[1] if passport else None,
        "authorization": {
            "live_id": entry.live_authorization_id,
            "source_id": source_id or auth.get("source_authorization_id"),
            "occurred_at": timestamp(entry.ts_sim),
            "merchant_id": entry.merchant_id,
            "amount": money(auth["amount"]) if auth.get("amount") is not None else None,
            "currency": auth.get("currency"),
            "billing_amount_chf": money(entry.billing_amount_chf),
            "items_hash": sha256_hex(items) if items is not None else None,
        },
        "permitted": permitted(evidence, checks),
        "evidence_hash": sha256_hex(evidence),
        "outcome": entry.outcome,
        "reason_codes": list(entry.reason_codes),
        "would_approve_if": entry.would_approve_if,
        "resolution": resolution(entry, device_id),
        "decided_at": entry.decided_at,
        "engine_version": entry.engine_version,
        "issuer": {"name": ISSUER, "key_id": key_id},
    })  # fmt: skip

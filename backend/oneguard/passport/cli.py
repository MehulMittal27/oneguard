"""The operator's terminal as a device (docs/passport.md §4, docs/demo-script.md pre-show).

Device-bound writes (confirm, revoke, device approve / remove) need a signature from a
device enrolled on the card, and in production nothing resets a card's devices. The
terminal therefore holds a device key of its own, the one ``make demo-live`` uses
(``viseca.demo.device_key_path``: ``~/.config/oneguard/device-<host>.pem``,
``ONEGUARD_DEVICE_KEY`` overrides), enrols it on the demo cards, and can then approve the
stage browser and a phone, or confirm and revoke policies, from the command line.

    python -m oneguard.passport.cli [--api URL] enrol CA0039 CA0023 [--label "Operator terminal"]
    python -m oneguard.passport.cli [--api URL] devices CA0039
    python -m oneguard.passport.cli [--api URL] approve CA0039 --label "Stage laptop"
    python -m oneguard.passport.cli [--api URL] remove CA0039 --label "Second phone"
    python -m oneguard.passport.cli [--api URL] confirm /tmp/draft-CA0039.json
    python -m oneguard.passport.cli [--api URL] revoke CA0001

``confirm`` takes a C1 draft as saved by the demo script's ``draft`` helper and confirms it
with its own checks. Exit 0 on success, 1 on a refusal (printed with its reason).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from oneguard.passport.signer import DeviceKey

API_ENV = "ONEGUARD_API_URL"
DEFAULT_API = "https://oneguard.fly.dev"
TIMEOUT_S = 30.0


class Refused(Exception):
    pass


class Terminal:
    """This terminal's device on each card, signing through ``http``."""

    def __init__(self, http: httpx.Client, key: DeviceKey, out: Callable[[str], None] = print) -> None:
        self.http = http
        self.key = key
        self.out = out
        self._ids: dict[str, str] = {}

    def call(self, method: str, path: str, body: Any = None, *, card: str | None = None) -> Any:
        headers = {}
        if card is not None:
            base = urlsplit(str(self.http.base_url)).path.rstrip("/")
            headers = self.key.headers(self.device_id(card), method, base + path, body)
        reply = self.http.request(method, path, json=body, headers=headers)
        if reply.is_success:
            return reply.json() if reply.content else None
        try:
            error = reply.json()["error"]
            raise Refused(f"{reply.status_code} {error['code']}: {error['message']}")
        except (ValueError, KeyError, TypeError):
            raise Refused(f"{reply.status_code}: {reply.text[:200]}") from None

    def enrol(self, card: str, label: str) -> dict[str, Any]:
        """Offer this terminal's key to the card: the card's first device is enrolled at once;
        the same key again returns the same device."""
        enrolled = self.call("POST", f"/api/cards/{card}/devices", {"public_key_jwk": self.key.jwk, "label": label})
        self._ids[card] = enrolled["device_id"]
        return enrolled

    def device_id(self, card: str) -> str:
        if card not in self._ids:
            self.enrol(card, default_label())
        return self._ids[card]

    def devices(self, card: str) -> list[dict[str, Any]]:
        return self.call("GET", f"/api/cards/{card}/devices")["devices"]

    def find(self, card: str, device: str | None, label: str | None, statuses: tuple[str, ...]) -> dict[str, Any]:
        """The one device on the card with this id, or with this label in one of ``statuses``."""
        rows = self.devices(card)
        if device:
            matches = [d for d in rows if d["device_id"] == device]
        else:
            matches = [d for d in rows if d["label"] == label and d["status"] in statuses]
        if len(matches) != 1:
            wanted = device or f"{label!r} ({' or '.join(statuses)})"
            raise Refused(f"{len(matches)} devices on {card} match {wanted}; name one by id")
        return matches[0]


def default_label() -> str:
    return f"Operator terminal on {socket.gethostname()}"


def main(argv: Sequence[str] | None = None, *, http: httpx.Client | None = None,
         key: DeviceKey | None = None, out: Callable[[str], None] = print) -> int:  # fmt: skip
    """``http``: a client already pointed at the server (tests); else one for ``--api``."""
    from oneguard.viseca.demo import device_key_path

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=os.environ.get(API_ENV, "").strip() or DEFAULT_API)
    commands = parser.add_subparsers(dest="command", required=True)
    enrol = commands.add_parser("enrol", help="offer this terminal's key to the cards")
    enrol.add_argument("cards", nargs="+")
    enrol.add_argument("--label", default=default_label())
    commands.add_parser("devices", help="list a card's devices").add_argument("card")
    for name in ("approve", "remove"):
        change = commands.add_parser(name, help=f"{name} a device on a card, signed by this terminal")
        change.add_argument("card")
        change.add_argument("device_id", nargs="?")
        change.add_argument("--label", help="the device's label instead of its id")
    commands.add_parser("confirm", help="confirm a saved C1 draft with its own checks").add_argument("draft", type=Path)
    commands.add_parser("revoke", help="revoke a card's policy").add_argument("card")
    args = parser.parse_args(argv)

    api = args.api.rstrip("/")
    key = key or DeviceKey.load_or_create(device_key_path(api))
    client = http or httpx.Client(base_url=api, timeout=TIMEOUT_S)
    try:
        return _run(Terminal(client, key, out), args)
    except Refused as exc:
        out(f"{api} refused: {exc}")
        return 1
    except httpx.HTTPError as exc:
        out(f"{api} did not answer: {type(exc).__name__}")
        return 1
    finally:
        if http is None:
            client.close()


def _run(terminal: Terminal, args: argparse.Namespace) -> int:
    out = terminal.out
    if args.command == "enrol":
        for card in args.cards:
            enrolled = terminal.enrol(card, args.label)
            note = "" if enrolled["status"] == "enrolled" else " - approve it from a device that controls the card"
            out(f"{card}: {enrolled['device_id']} {enrolled['status']}{note}")
        return 0
    if args.command == "devices":
        for d in terminal.devices(args.card):
            out(f"{d['device_id']}  {d['status']:<8}  {d['label']}")
        return 0
    if args.command in ("approve", "remove"):
        if not args.device_id and not args.label:
            raise Refused("name the device: its id, or --label")
        statuses = ("pending",) if args.command == "approve" else ("enrolled", "pending")
        device = terminal.find(args.card, args.device_id, args.label, statuses)
        changed = terminal.call(
            "POST", f"/api/cards/{args.card}/devices/{device['device_id']}/{args.command}", card=args.card
        )
        out(f"{args.card}: {changed['label']} is {changed['status']}")
        return 0
    if args.command == "confirm":
        draft = json.loads(args.draft.read_text(encoding="utf-8"))
        body = {k: draft[k] for k in ("checks", "uncertainty_policy", "open_questions")}
        mandate = terminal.call("POST", f"/api/policy-drafts/{draft['draft_id']}/confirm", body, card=draft["card_id"])
        passport = (mandate.get("passport") or {}).get("version")
        out(f"{mandate['card_id']}: policy {mandate['mandate_id']} {mandate['status']}, passport version {passport}")
        return 0
    terminal.call("POST", f"/api/cards/{args.card}/policy/revoke", card=args.card)
    out(f"{args.card}: policy revoked")
    return 0


if __name__ == "__main__":
    sys.exit(main())

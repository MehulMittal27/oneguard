#!/usr/bin/env python3
"""
Checks this UI's mock fixtures against the backend's frozen wire shapes
(backend/oneguard/api/models.py, docs/team-contract.md §3).

Why this exists: those models are `extra="forbid"`, so any field the fixtures
invent — or any required field they omit — is a payload the real API would
reject or never send. Catching that here means the swap to a real backend is
wiring, not rework, and it can be checked before any HTTP route exists.

Parses models.py with `ast` rather than importing it, so it needs no venv, no
pydantic and no network. Structural only: field names, required/optional and
Literal values, not types or numeric bounds. Read-only on backend/.

Run: python3 frontend/scripts/check_contract.py
"""

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS = REPO_ROOT / "backend" / "oneguard" / "api" / "models.py"
FIXTURES = REPO_ROOT / "frontend" / "src" / "mocks" / "fixtures"

# Keys the fixture builders add for the UI's own use and that no API ever
# sends. Documented in the frontend's own working rules (fixtures are marked
# `mock: true`); `scenario_id` is how a fixture row is traced back to the data
# pack by hand, never read by a component.
MOCK_ONLY = {"mock", "scenario_id"}


def load_models():
    """{class name: (fields, required, literals)} for every ApiModel subclass."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    models = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(getattr(b, "id", None) == "ApiModel" for b in node.bases):
            continue
        fields, required, literals = set(), set(), {}
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            name = stmt.target.id
            if name.startswith("_"):
                continue
            fields.add(name)
            # A field is optional to *send* only when it has a default.
            if stmt.value is None:
                required.add(name)
            annotation = ast.unparse(stmt.annotation)
            values = literal_values(annotation)
            if values:
                literals[name] = values
        models[node.name] = (fields, required, literals)
    return models


def literal_values(annotation: str):
    """The allowed strings of a `Literal[...]` annotation, if it is one."""
    if "Literal[" not in annotation:
        return None
    inner = annotation.split("Literal[", 1)[1]
    depth, end = 1, 0
    for i, ch in enumerate(inner):
        depth += (ch == "[") - (ch == "]")
        if depth == 0:
            end = i
            break
    out = set()
    for part in inner[:end].split(","):
        part = part.strip()
        if part[:1] in {'"', "'"} and part[-1:] in {'"', "'"}:
            out.add(part[1:-1])
    return out or None


def check(rows, model_name, models, problems, label):
    fields, required, literals = models[model_name]
    for row in rows:
        keys = set(row) - MOCK_ONLY
        rid = row.get("authorization_id") or row.get("customer_id") or row.get("draft_id") or "?"
        for extra in sorted(keys - fields):
            problems.append(f"{label} {rid}: field {extra!r} is not on {model_name} (extra=forbid)")
        for missing in sorted(required - keys):
            problems.append(f"{label} {rid}: required field {missing!r} missing from {model_name}")
        for key, allowed in literals.items():
            if key in row and row[key] is not None and row[key] not in allowed:
                problems.append(
                    f"{label} {rid}: {key}={row[key]!r} not in {sorted(allowed)} ({model_name})"
                )


def main():
    if not MODELS.exists():
        print(f"skip: {MODELS.relative_to(REPO_ROOT)} not on this branch yet")
        return 0

    models = load_models()
    problems: list[str] = []

    decisions = json.loads((FIXTURES / "decisions.json").read_text())["decisions"]
    check(decisions, "Decision", models, problems, "decision")
    for d in decisions:
        check(d.get("items", []), "DecisionItem", models, problems, "item")
        check(d.get("evidence", []), "Evidence", models, problems, "evidence")

    customers = json.loads((FIXTURES / "customers.json").read_text())["customers"]
    check(customers, "Customer", models, problems, "customer")

    accounts = json.loads((FIXTURES / "accounts.json").read_text())["accounts"]
    check(accounts, "Account", models, problems, "account")
    for a in accounts:
        check(a.get("cards", []), "Card", models, problems, "card")

    drafts = json.loads((FIXTURES / "policy-drafts.json").read_text())["drafts"]
    # `usage` rides on the draft so mock confirmPolicy can copy it onto the
    # Mandate it returns, as a real C2 would; it is not a PolicyDraft field.
    check([{k: v for k, v in d.items() if k != "usage"} for d in drafts],
          "PolicyDraft", models, problems, "draft")
    for d in drafts:
        check([d["dry_run"]], "DryRunResult", models, problems, "dry_run")
        check(d["dry_run"].get("examples", []), "DryRunExample", models, problems, "example")
        check(d["checks"], "RuleCheck", models, problems, "check")
        if "usage" in d:
            check([d["usage"]], "MandateUsage", models, problems, "usage")

    counts = f"{len(decisions)} decisions, {len(customers)} customers, {len(accounts)} accounts, {len(drafts)} drafts"
    if problems:
        print(f"FAIL — {len(problems)} mismatch(es) against api/models.py ({counts}):")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"PASS — {counts} match the backend's frozen wire shapes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

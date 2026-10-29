"""Fail-closed Inbox admission shared by every owner-side posting transport."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

INTERACTIONS = frozenset({"approval", "credential", "publish_as_david", "physical", "preference"})


def family(poster):
    return "-".join(str(poster or "unlabelled").split("·", 1)[0].strip().lower().replace("_", " ").split())


def registry(home):
    # JSON is a YAML 1.2 subset: no runtime YAML dependency for this safety gate.
    rows = json.loads(Path(__file__).with_name("posters.yaml").read_text())
    extra = Path(home) / "inbox" / "posters.yaml"
    if extra.exists():
        rows.update(json.loads(extra.read_text()))
    return rows


def test_isolation(home):
    if not (os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("HERMES_TEST")):
        return False
    target = Path(home).expanduser().resolve()
    roots = [Path.home() / ".hermes", Path.home() / ".bb-hermes"]
    return any(target == r.resolve() or target.is_relative_to((r / "profiles").resolve()) for r in roots)


_TEST = re.compile(r"\b(?:TEST|scratch|drill|ignore)\b", re.I)
_STATUS = re.compile(r"\b(?:was rejected|failed|expired|came back|missing guards|still on disk|alert|sent to)\b|\bposted:|^Fixed:", re.I)


def reason(*, home, poster, ask, actions, type_name="default", title="", body="", key="", **_):
    if _TEST.search(title) or _TEST.search(str(key or "")):
        return "content_test"
    if not poster or not str(poster).strip():
        return "poster_missing"
    row = registry(home).get(family(poster))
    if not row:
        return "poster_unregistered"
    if type_name not in row["allowed_types"] and "*" not in row["allowed_types"]:
        return "poster_type_forbidden"
    if any(a.get("kind") == "resolve" for a in actions):
        return None
    if _STATUS.search(title) or _STATUS.search(body):
        return "content_status"
    if not ask:
        return "ask_missing"
    if not isinstance(ask, dict) or ask.get("interaction") not in INTERACTIONS:
        return "ask_invalid"
    if any(not isinstance(ask.get(k), str) or not ask[k].strip() for k in ("why_human", "action")):
        return "ask_invalid"
    return None

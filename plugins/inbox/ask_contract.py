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


# Upper-case TEST only: real titles say "wave-speed test". Fixture signatures are matched case-insensitively.
_TEST_CS = re.compile(r"\bTEST\b")
_TEST_CI = re.compile(r"\b(?:scratch|drill|ignore)\b|\b(?:live|e2e|smoke) test\b|\btest (?:card|post|ask)\b|CTESTLIVE|9999-\d\d-\d\d", re.I)


# Test markers inside @handles: scratch anywhere (@scratchtest, @myscratch) and a standalone "test" segment
# (@test, @test_account, @acme-test, @acme_test2). "test" inside an ordinary word (@latest, @contest, @testarossa)
# is a real account and passes.
_TEST_HANDLE = re.compile(r"@(?:[\w.\-]*scratch|(?:[\w.\-]*[_.\-])?test\d*(?![A-Za-z]))", re.I)


def _is_test(text):
    return bool(_TEST_CS.search(text) or _TEST_CI.search(text) or _TEST_HANDLE.search(text))


def is_test(text):
    return _is_test(text or "")


RETRY_SHAPE = ('retry the same post with ask={"interaction": "approval|credential|publish_as_david|physical|preference", '
               '"why_human": "<why only David can do this>", "action": "<the exact thing David does>"} '
               '(Inbox tool: pass `ask`; CLI: --ask JSON). Post it only if David must act; otherwise use a Kanban card')

_HR_INTERACTION = {"approval": "approval", "credential": "credential", "physical": "physical",
                   "user_terminal": "physical", "reply": "preference", "external": "approval"}


def ask_from_human_request(hr):
    """The Inbox ask for a Kanban card's human_request (dict or JSON text), or None when it has none."""
    if isinstance(hr, str):
        try:
            hr = json.loads(hr)
        except ValueError:
            return None
    if not isinstance(hr, dict):
        return None
    interaction = _HR_INTERACTION.get(str(hr.get("interaction") or "").strip())
    why, action = hr.get("why_human"), hr.get("action")
    if not interaction or not all(isinstance(v, str) and v.strip() for v in (why, action)):
        return None
    return {"interaction": interaction, "why_human": why.strip(), "action": action.strip()}
_STATUS = re.compile(r"\b(?:was rejected|failed|expired|came back|missing guards|still on disk|sent to)\b|\bposted:|^Fixed:", re.I)


# Outgoing-action notices: the type's resolve actions do not make "X DM sent" a decision.
_SENT_NOTICE = re.compile(r"^\s*X\s+(?:DM\s+|reply\s+|post\s+)?(?:sent|posted)\b", re.I)


def reason(*, home, poster, ask, actions, type_name="default", title="", body="", key="", **_):
    if _is_test(title) or _is_test(str(key or "")):
        return "content_test"
    if not poster or not str(poster).strip():
        return "poster_missing"
    row = registry(home).get(family(poster))
    if not row:
        return "poster_unregistered"
    if type_name not in row["allowed_types"] and "*" not in row["allowed_types"]:
        return "poster_type_forbidden"
    if _SENT_NOTICE.search(title or ""):
        return "content_status"
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

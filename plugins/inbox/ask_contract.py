"""Fail-closed Inbox admission shared by every owner-side posting transport."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

INTERACTIONS = frozenset({"approval", "credential", "publish_as_david", "physical", "preference"})


def family(poster):
    return "-".join(str(poster or "unlabelled").split("·", 1)[0].strip().lower().replace("_", " ").split())


def runtime_family():
    """The agent-owned family the runtime context proves, or None: a Kanban worker (any door) or a cron run."""
    # The terminal tool strips HERMES_KANBAN_TASK from shell children; HERMES_KANBAN_WORKER_TASK survives there.
    if os.environ.get("HERMES_KANBAN_TASK") or os.environ.get("HERMES_KANBAN_WORKER_TASK"):
        return "kanban"
    cron = os.environ.get("HERMES_CRON_SESSION", "")
    if not cron:
        try:  # cron binds it as a ContextVar, not os.environ
            from gateway.session_context import get_session_env
            cron = get_session_env("HERMES_CRON_SESSION", "")
        except Exception:
            cron = ""
    return "cron" if str(cron).strip().lower() in ("1", "true", "yes", "on") else None


def effective_poster(home, poster):
    """Bind the caller-declared label to the runtime. The label is trusted only when nothing contradicts it:
    inside a Kanban/cron run a poster the registry leaves unrestricted (`*`) and non-agent-owned (cli, desktop,
    oneshot) is re-labelled `<runtime> · <declared>`, so the registry lookup, AGENT_OWNERS and the noise breaker
    all see the runtime family. Restricted posters (social, slack, coach) keep their own bounded type lists."""
    rt = runtime_family()
    if not rt or not poster or family(poster) == rt:
        return poster
    row = registry(home).get(family(poster))
    if row and row.get("owner") not in AGENT_OWNERS and "*" in row.get("allowed_types", []):
        return f"{rt} · {poster}"[:80]
    return poster


def declared_label(poster):
    """The caller-declared label of an effective poster built by `effective_poster`, else None."""
    head, sep, rest = str(poster or "").partition(" · ")
    return rest if sep and head in ("kanban", "cron") and family(rest) in ("cli", "desktop", "oneshot") else None


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


# Defect wording, checked on agent-owned resolve-typed titles only ("X misses Y", "is stuck", "can't ...").
_DEFECT = re.compile(r"\b(?:misses|wrongly|crash(?:es|ed|ing)?|stall(?:s|ed|ing)?|stuck|broken|unnoticed|"
                     r"does not|cannot|(?:doesn|don|can|won|isn|aren)[’']t|fails?|failing|errors?|bugs?)\b", re.I)
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")


# Agent-owned posters (registry owner kanban/coach/cron) can attest any ask themselves, so an ask counts only when it
# came from the card's human_request. No key exemption: keys are caller-chosen, so a key allowlist is a bypass. A
# recurring owner decision (spend cap) uses a resolve-typed card its registry row lists (no "*" for agent owners),
# and that title still passes the status/defect backstop. `defect_titles` (self-heal) opts out: its titles name a
# defect by design and selfheal_reason in store.py gates them.
AGENT_OWNERS = frozenset({"kanban", "coach", "cron"})
SELF_ATTESTED_MSG = ("this poster is agent-owned and the ask is self-attested. If David must act, block your card "
                     "with kanban_block(human_request={...}) and the Inbox ask is derived from it; otherwise fix it "
                     "or use a Kanban card")


def missing_poster_hint(home, type_name=None):
    """Retry text for poster_missing_keyed: the registered families that may post this card type."""
    t = type_name or "default"
    fams = sorted(f for f, row in registry(home).items()
                  if t in row.get("allowed_types", ()) or "*" in row.get("allowed_types", ()))
    return (f"this keyed post named no poster, so it was labelled 'key · <prefix>', which is never registered. "
            f"Pass --poster <family> (CLI), poster= (tool/API) or \"poster\" in the inbox_guard payload; "
            f"families allowed to post type {t!r}: {', '.join(fams) or 'none (register one in posters.yaml)'}")


def reason(*, home, poster, ask, actions, type_name="default", title="", body="", key="",
           from_human_request=False, **_):
    if _is_test(title) or _is_test(str(key or "")):
        return "content_test"
    if not poster or not str(poster).strip():
        return "poster_missing"
    reg = registry(home)
    row = reg.get(family(poster))
    if not row:
        # A keyed post that named no poster gets the derived "key · <prefix>" (store.key_poster). That family is never
        # registered, so it can never pass: say so, and missing_poster_hint() names what to pass (cron-3299286b9a).
        return "poster_missing_keyed" if family(poster) == "key" else "poster_unregistered"
    if type_name not in row["allowed_types"] and "*" not in row["allowed_types"]:
        return "poster_type_forbidden"
    if _SENT_NOTICE.search(title or ""):
        return "content_status"
    if any(a.get("kind") == "resolve" for a in actions):
        # A decision type carries its own ask, but for an agent-owned poster it is no way around the wording
        # backstop: "Detector misses replies" on an engine-budget card is still agent work.
        if row.get("owner") in AGENT_OWNERS and not row.get("defect_titles"):
            own = _QUOTED.sub(" ", title or "")  # quoted third-party text (a reply draft's tweet) is not ours
            if _STATUS.search(own):
                return "content_status"
            if _DEFECT.search(own):
                return "content_defect"
        return None
    if _STATUS.search(title) or _STATUS.search(body):
        return "content_status"
    if not ask:
        return "ask_missing"
    if not isinstance(ask, dict) or ask.get("interaction") not in INTERACTIONS:
        return "ask_invalid"
    if any(not isinstance(ask.get(k), str) or not ask[k].strip() for k in ("why_human", "action")):
        return "ask_invalid"
    if row.get("owner") in AGENT_OWNERS and not from_human_request:
        return "ask_self_attested"
    return None

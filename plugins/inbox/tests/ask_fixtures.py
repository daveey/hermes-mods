"""Explicit valid human-decision payload for lifecycle/routing fixtures.

These tests predate admission. Their store calls must carry valid asks now; the
real gate still runs. Admission and transport tests never use this helper.
"""
from functools import partial

ASK = {"interaction": "preference", "why_human": "Only the owner can choose the proposed wording", "action": "Choose this wording"}


def decision_inbox(box):
    box.post = partial(box.post, poster="cli", ask=ASK)
    return box


TEST_POSTERS = ("p", "q", "x", "t", "agent", "someone", "p-fenced", "cron", "kanban", "coach", "self-heal",
                "nightly-audit", "twitter-post-monitor", "kanban-dispatcher", "rest-poster")


def register_test_posters(home, extra=()):
    """Test-local registry extras (home/inbox/posters.yaml). The shipped registry is untouched."""
    import json
    from pathlib import Path
    path = Path(home) / "inbox" / "posters.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(path.read_text()) if path.exists() else {}
    for name in (*TEST_POSTERS, *extra):
        rows.setdefault(name, {"owner": "test", "allowed_types": ["*"]})
    path.write_text(json.dumps(rows))


def with_default_ask(box):
    """Like decision_inbox but a caller-supplied poster/ask still wins; the real gate still runs."""
    real = box.post

    def post(*a, **kw):
        kw.setdefault("ask", ASK)
        return real(*a, **kw)
    box.post = post
    return box

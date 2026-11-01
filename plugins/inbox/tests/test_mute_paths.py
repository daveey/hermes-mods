"""Noise breaker on every post entry path: CLI (`hermes inbox post`), the inbox_post tool, REST.

Run like the other inbox tests (see test_api.py docstring).
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _load_plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("_inbox_plugin_mute_test", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = mod.store_module()
    monkeypatch.setattr(store, "kanban_task_creator", lambda **kw: "t_test")  # no real board
    import sys
    sys.path.insert(0, str(HERE / "tests"))
    from ask_fixtures import register_test_posters
    register_test_posters(tmp_path)
    return mod, store


def _mute(store, poster):
    from ask_fixtures import ASK
    inbox = store.Inbox()
    for i in range(store.MUTE_AFTER):
        inbox.archive(inbox.post(f"noise {i}", "b", "do", key=f"n{i}", poster=poster, ask=ASK)["session_id"])


def _cli(mod, argv):
    p = argparse.ArgumentParser()
    mod._setup_cli(p)
    return mod._run_cli(p.parse_args(argv))


_ASK_JSON = json.dumps({"interaction": "preference", "why_human": "Only the owner can choose the proposed wording",
                        "action": "Choose this wording"})
_FIX = "abcdef1234567"


def test_cli_post_refused_then_unmute(monkeypatch, tmp_path, capsys):
    mod, store = _load_plugin(monkeypatch, tmp_path)
    _mute(store, "cron · noisy")
    post = ["post", "again", "--body", "b", "--prompt", "do", "--poster", "cron · noisy", "--ask", _ASK_JSON]
    assert _cli(mod, post) == 1
    assert "muted" in capsys.readouterr().err
    assert _cli(mod, ["unmute", "cron · noisy", "--fix-commit", "not-a-sha"]) == 1  # needs a real commit
    assert _cli(mod, ["unmute", "cron · noisy", "--fix-commit", _FIX]) == 0
    assert _cli(mod, post) == 0


def test_tool_post_refused(monkeypatch, tmp_path):
    mod, store = _load_plugin(monkeypatch, tmp_path)
    _mute(store, "kanban · noisy worker")
    monkeypatch.setattr(mod, "_poster_for", lambda sid: ("kanban · noisy worker", None))
    out = json.loads(mod.handle_post({"title": "again", "body": "b", "accept_prompt": "do",
                                      "ask": json.loads(_ASK_JSON)}, session_id="s"))
    assert out["posted"] is False and "PosterMuted" in out["error"]


def test_rest_post_refused(monkeypatch, tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("_inbox_api_mute_test", HERE / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "_profile", lambda: "default")
    store = api._store()
    monkeypatch.setattr(store, "kanban_task_creator", lambda **kw: "t_test")
    sys.path.insert(0, str(HERE / "tests"))
    from ask_fixtures import register_test_posters
    register_test_posters(tmp_path)
    _mute(store, "rest-poster")
    app = fastapi.FastAPI()
    app.include_router(api.router)
    r = TestClient(app).post("/items", json={"title": "again", "body": "b", "accept_prompt": "do",
                                             "poster": "rest-poster", "ask": json.loads(_ASK_JSON)})
    assert r.status_code == 400 and "muted" in r.text


# The exact post 1e6f307c7e (hermes core) made for every respawn-guard hold.
_GUARD_HOLD_PROMPT = ("Look at kanban card t_x: it is held by the respawn guard. Read its latest "
                      "[respawn-guard] comment, fix the cause (failing PR checks, binding) and unblock it.")


def test_agent_fixable_posts_refused_before_any_card_exists(monkeypatch, tmp_path, capsys):
    """Detection-level guard: no 3-archive noise breaker needed; nothing reaches David. [daveey]"""
    mod, store = _load_plugin(monkeypatch, tmp_path)
    inbox = store.Inbox()
    with pytest.raises(store.AgentFixablePost):
        inbox.post("Kanban t_x held by respawn guard (active_pr)", "b", _GUARD_HOLD_PROMPT,
                   key="guard-hold:t_x", poster="kanban-dispatcher")
    with pytest.raises(store.AgentFixablePost):  # same prompt, any poster
        inbox.post("held", "b", _GUARD_HOLD_PROMPT, poster="cron · something")
    with pytest.raises(store.AgentFixablePost):  # the dispatcher poster, any prompt
        inbox.post("held", "b", "Approve?", poster="Kanban-Dispatcher")
    assert inbox.list() == []
    assert _cli(mod, ["post", "held", "--body", "b", "--prompt", _GUARD_HOLD_PROMPT,
                      "--key", "guard-hold:t_x", "--poster", "kanban-dispatcher"]) == 1
    assert "kanban" in capsys.readouterr().err.lower()
    monkeypatch.setattr(mod, "_poster_for", lambda sid: ("kanban-dispatcher", None))
    out = json.loads(mod.handle_post({"title": "held", "body": "b", "accept_prompt": "do"}, session_id="s"))
    assert out["posted"] is False and "AgentFixablePost" in out["error"]


def test_owner_decisions_still_post(monkeypatch, tmp_path):
    mod, store = _load_plugin(monkeypatch, tmp_path)
    inbox = store.Inbox()
    from ask_fixtures import ASK
    inbox.post("Approve the Q3 budget?", "b", "Approve it and tell finance.", poster="cron · finance", ask=ASK)
    inbox.post("Unblock t_y?", "Which DB?", "Unblock kanban card t_y with answer: use Postgres.",
               poster="kanban · hands digest", ask=ASK)
    assert len(inbox.list()) == 2


# ── typed decisions reset the streak; every mute refusal has an owner card (self-heal cron-3299286b9a) ──

def _typed_env(tmp_path, monkeypatch):
    """A real store whose conditional path (`if_absent`) is the one x_reply_watch uses."""
    sys.path.insert(0, str(HERE / "tests"))
    from test_store import store as st
    from hermes_state import SessionDB
    from ask_fixtures import decision_inbox, register_test_posters
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    clock = {"t": 1_000_000.0}
    register_test_posters(tmp_path)
    cards = []
    inbox = decision_inbox(st.Inbox(home=tmp_path, db_factory=lambda: db, clock=lambda: clock["t"],
                                    task_creator=lambda **kw: cards.append(kw) or "t_mute"))
    inbox.types.define("draft-post", {
        "label": "Draft", "fields": {"text": {"required": True}},
        "actions": [{"id": "post", "label": "Post", "kind": "prompt", "prompt": "Publish {text}"}]})
    return st, inbox, db, clock, cards


def _seq(inbox, clock, outcomes, type_="draft-post", poster="p"):
    for i, how in enumerate(outcomes):
        clock["t"] += 10
        kw = dict(type=type_, fields={"text": f"t{i}"}) if type_ != "default" else {}
        sid = inbox.post(f"Draft {i}", "b", "Do it", key=f"draft:{i}", poster=poster, **kw)["session_id"]
        clock["t"] += 1
        if how == "archive":
            inbox.archive(sid)
        else:
            inbox.act(sid, how)
    clock["t"] += 10


def test_typed_accept_breaks_ignored_streak(tmp_path, monkeypatch):
    """archived, accepted(post), archived: David's 'post' is a decision, so the next draft lands."""
    st, inbox, db, clock, cards = _typed_env(tmp_path, monkeypatch)
    _seq(inbox, clock, ["archive", "post", "archive"])
    out = inbox.post("Draft new", "b", "Do it", key="draft:new", poster="p", type="draft-post",
                     fields={"text": "n"}, if_absent=True)
    assert out["applied"] is True
    db.close()


def test_generic_do_it_does_not_break_streak(tmp_path, monkeypatch):
    """archived, accepted(do_it), archived: a generic Do it is not a decision; still muted."""
    st, inbox, db, clock, cards = _typed_env(tmp_path, monkeypatch)
    _seq(inbox, clock, ["archive", "do_it", "archive"], type_="default")
    with pytest.raises(st.PosterMuted):
        inbox.post("Again", "b", "Do it", key="draft:new", poster="p", if_absent=True)
    db.close()


def test_conditional_mute_files_owner_card(tmp_path, monkeypatch):
    """A conditional post refused as muted files the inbox-muted:<family> owner card (was silent)."""
    st, inbox, db, clock, cards = _typed_env(tmp_path, monkeypatch)
    _seq(inbox, clock, ["archive", "archive"])
    with pytest.raises(st.PosterMuted):
        inbox.post("Draft new", "b", "Do it", key="draft:new", poster="p", type="draft-post",
                   fields={"text": "n"}, if_absent=True)
    assert [c["idempotency_key"] for c in cards] == ["inbox-muted:p"]
    db.close()


def test_every_poster_muted_raise_goes_through_check_helpers():
    """Detection guard: a `raise PosterMuted` outside the owner-filing helpers is a silent mute path."""
    import ast
    tree = ast.parse((HERE / "store.py").read_text())
    allowed = {"_check_mute", "_check_junk_mute"}
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if isinstance(exc, ast.Name) and exc.id == "PosterMuted" and fn.name not in allowed:
                    bad.append(f"{fn.name}:{node.lineno}")
    assert not bad, f"raise PosterMuted outside {sorted(allowed)}: {bad}"
    helpers = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name in allowed}
    assert set(helpers) == allowed
    for name, f in helpers.items():  # each helper files a card before raising
        calls = {n.func.attr for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert calls & {"_file_card", "_file_mute_card"}, name

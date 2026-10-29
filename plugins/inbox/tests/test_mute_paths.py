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

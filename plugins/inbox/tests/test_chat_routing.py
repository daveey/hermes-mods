"""Exercise the registered hook (not just the classifier), with owner-scoped stores."""
import contextlib
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_social_routing import make_projects

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("inbox_chat_test", HERE / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    target = tmp_path / "pr"
    target.mkdir()
    make_projects(tmp_path, str(target))
    row = {"cwd": None}
    session = dict(session_key="chat", source="desktop", explicit_cwd=False,
                   history_lock=threading.Lock(), profile_home=str(tmp_path), cwd=str(tmp_path))
    calls = []
    def setter(s, cwd):
        calls.append(cwd)
        s.update(cwd=cwd, explicit_cwd=True)
        row["cwd"] = cwd
        return cwd
    server = SimpleNamespace(_sessions_lock=threading.Lock(), _sessions={"runtime": session},
        _session_db=lambda s: contextlib.nullcontext(SimpleNamespace(get_session=lambda key: row)),
        _set_session_cwd=setter, _emit=lambda *args: None, _cwd_info=lambda s, cwd: {"cwd": cwd})
    monkeypatch.setitem(sys.modules, "tui_gateway.server", server)
    hooks = {}
    ctx = SimpleNamespace(register_tool=lambda **kw: None,
                          register_hook=lambda key, fn: hooks.update({key: fn}),
                          register_cli_command=lambda *a, **kw: None)
    plugin.register(ctx)
    def run(**kwargs):
        args = dict(session_id="chat", user_message="Draft a Bluesky post about PR #123",
                    is_first_turn=True, platform="desktop")
        args.update(kwargs)
        return hooks["pre_llm_call"](**args)
    return session, row, calls, run, str(target)


@pytest.mark.parametrize("surface", ["desktop", "tui"])
def test_registered_first_turn_hook_routes_and_persists(chat, surface):
    s, row, calls, run, target = chat
    s["source"] = surface
    assert run(platform=surface)["context"]
    assert calls == [target] and row["cwd"] == target and s["cwd"] == target
    assert run() is None
    assert calls == [target]


@pytest.mark.parametrize("changes", [dict(explicit_cwd=True), dict(cwd_pinned=True),
    dict(parent_session_id="parent"), dict(source="kanban"), dict(source="cli"),
    dict(_closing=True), dict(room_plumbing=True)])
def test_explicit_project_and_workers_are_untouched(chat, changes):
    s, row, calls, run, target = chat
    s.update(changes)
    assert run() is None
    assert calls == []


@pytest.mark.parametrize("args", [dict(is_first_turn=False), dict(parent_session_id="parent"),
    dict(platform="kanban"), dict(session_id="foreign"),
    dict(user_message="Merge PR #123 for Twitter post scheduler"),
    dict(user_message="Fix X reply API retry bug")])
def test_only_matching_owned_first_turn(chat, args):
    s, row, calls, run, target = chat
    assert run(**args) is None
    assert not calls


def test_persisted_explicit_home_is_not_overridden(chat):
    s, row, calls, run, target = chat
    row["cwd"] = str(Path.home())
    assert run() is None
    assert not calls


def test_worker_environment_is_excluded(chat, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_example")
    assert chat[3]() is None
    assert not chat[2]


def test_owner_profile_project_not_process_home(chat, tmp_path, monkeypatch):
    s, row, calls, run, target = chat
    owner = tmp_path / "foreign-owner"
    owner.mkdir()
    owner_pr = owner / "pr"
    owner_pr.mkdir()
    make_projects(owner, str(owner_pr), pid="p_owner")
    s["profile_home"] = str(owner)
    assert run()["context"]
    assert row["cwd"] == str(owner_pr)
    assert calls == [str(owner_pr)]

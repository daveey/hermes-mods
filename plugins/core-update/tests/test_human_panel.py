"""Tests for human-readable update summaries, session unblock links, and blocker controls."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import time

import pytest

BASE = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"test_cu_{name}", BASE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_state_db(home: Path, sessions: dict[str, str], leases: dict[str, str] | None = None) -> None:
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "state.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, display_name TEXT)")
    conn.execute(
        "CREATE TABLE session_turn_leases (conversation_id TEXT PRIMARY KEY, holder TEXT, expires_at REAL)"
    )
    conn.execute(
        "CREATE TABLE session_turn_end_requests (conversation_id TEXT PRIMARY KEY, holder TEXT, requested_at REAL, reason TEXT)"
    )
    for sid, title in sessions.items():
        conn.execute("INSERT INTO sessions (id, title, display_name) VALUES (?, ?, ?)", (sid, title, None))
    now = time.time()
    for sid, holder in (leases or {}).items():
        conn.execute(
            "INSERT INTO session_turn_leases (conversation_id, holder, expires_at) VALUES (?, ?, ?)",
            (sid, holder, now + 600),
        )
    conn.commit()
    conn.close()


def _seed_kanban_db(home: Path, *, tasks: list[tuple[str, str, str]], wake_checks: list[tuple[str, str]], links: list[tuple[str, str, str]]) -> None:
    conn = sqlite3.connect(home / "kanban.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, worker_pid INTEGER, claim_lock TEXT, lease_holder TEXT, started_at REAL)"
    )
    conn.execute("CREATE TABLE task_wake_checks (task_id TEXT, cmd TEXT)")
    conn.execute(
        "CREATE TABLE task_chat_links (task_id TEXT, chat_profile TEXT, chat_session TEXT, released INTEGER DEFAULT 0)"
    )
    for tid, title, status in tasks:
        conn.execute("INSERT INTO tasks (id, title, status, worker_pid) VALUES (?, ?, ?, ?)", (tid, title, status, os.getpid()))
    for tid, cmd in wake_checks:
        conn.execute("INSERT INTO task_wake_checks (task_id, cmd) VALUES (?, ?)", (tid, cmd))
    for tid, prof, sid in links:
        conn.execute("INSERT INTO task_chat_links (task_id, chat_profile, chat_session, released) VALUES (?, ?, ?, 0)", (tid, prof, sid))
    conn.commit()
    conn.close()


def _seed_cron(home: Path, jobs: dict[str, str], running_execs: list[tuple[str, str, int]]) -> None:
    cron_dir = home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": jid, "name": name} for jid, name in jobs.items()]})
    )
    conn = sqlite3.connect(cron_dir / "executions.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE executions (id TEXT PRIMARY KEY, job_id TEXT, pid INTEGER, process_started_at INTEGER, status TEXT, claimed_at TEXT, started_at TEXT)"
    )
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 185))
    for eid, jid, pid in running_execs:
        conn.execute(
            "INSERT INTO executions (id, job_id, pid, process_started_at, status, claimed_at, started_at) VALUES (?, ?, ?, NULL, 'running', ?, ?)",
            (eid, jid, pid, started_iso, started_iso),
        )
    conn.commit()
    conn.close()


def test_summary_links_paused_and_unblocked_sessions_without_git_hashes(tmp_path):
    summary = load("summary")
    home = tmp_path / "home"
    _seed_state_db(home, {
        "s_paused": "Switch model sessions to fallback route",
        "s_blocked": "Resolve outside chat asks",
    })
    (home / "desktop").mkdir(parents=True, exist_ok=True)
    (home / "desktop" / "interrupted_turns.json").write_text(json.dumps({
        "s_paused": {"reason": "paused_for_update", "auto_continue": True, "writer_pid": os.getpid()},
    }))
    sha_offer = "081099bba053e000000000000000000000000001"
    sha_wake = "355ef7a427d43000000000000000000000000002"
    sha_test = "a61d6a3958000000000000000000000000000003"
    _seed_kanban_db(
        home,
        tasks=[("t_01a20505d1103ebf", "fix(kanban): retain release offers", "scheduled")],
        wake_checks=[("t_01a20505d1103ebf", f"git merge-base --is-ancestor {sha_offer} HEAD")],
        links=[("t_01a20505d1103ebf", "default", "s_blocked")],
    )
    commits = [
        {"sha": sha_test, "subject": "test(kanban): make launcher parity clock-independent", "body": ""},
        {"sha": sha_wake, "subject": "fix(session-state): wake unknown dependencies on local arrival [author]", "body": ""},
        {"sha": sha_offer, "subject": "fix(kanban): retain release offers in requesting chats", "body": ""},
    ]
    out = summary.build_summary(
        home,
        tmp_path / "state",
        running="1af856a521000000000000000000000000000000",
        target=sha_test,
        commits=commits,
        desktop_swap_pending=False,
        active_update=True,
        allow_async_llm=False,
    )
    bullets = out["bullets"]
    joined = " | ".join(bullets)
    assert any("Resumes “Switch model sessions to fallback route”" in b for b in bullets)
    assert any("Unblocks “Resolve outside chat asks”" in b for b in bullets)
    assert any("session status" in b.lower() or "wakes" in b.lower() for b in bullets)
    # Pure test commit is omitted when user-facing commits exist, and zero hashes/card IDs leak.
    assert "clock-independent" not in joined
    for forbidden in ("081099bb", "355ef7a4", "a61d6a39", "1af856a5", "t_01a20505", "[author]"):
        assert forbidden not in joined


def test_pool_llm_summary_falls_back_from_exhausted_provider_and_caches(tmp_path):
    summary = load("summary")
    pool_bin = tmp_path / "hermes-pool-llm"
    pool_bin.write_text("#!/bin/sh\n")
    cache_file = tmp_path / "summaries" / "digest.json"
    lock_file = tmp_path / "summaries" / "digest.lock"
    calls = []

    def fake_runner(argv, **kwargs):
        provider = argv[argv.index("--provider") + 1]
        calls.append(provider)
        if provider == "anthropic":
            return SimpleNamespace(returncode=2, stdout="", stderr="pool exhausted")
        payload = {
            "bullets": [
                "Unblocks “Resolve outside chat asks” by keeping release offers in the requesting chat (081099bb)",
                "Fixes session status waking as soon as dependencies arrive [author] (t_01a20505d1103ebf)",
            ]
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    commits = [{"sha": "081099bba053e000", "subject": "fix(kanban): retain release offers", "body": ""}]
    impacts = [{"sha": "081099bba053e000", "session": "Resolve outside chat asks", "task": "retain release offers"}]
    bullets = summary._run_pool_summary(pool_bin, cache_file, lock_file, commits, impacts, runner=fake_runner)
    assert calls == ["anthropic", "openai-codex"]
    assert bullets is not None and len(bullets) == 2
    assert "081099bb" not in " ".join(bullets)
    assert "t_01a20505d1103ebf" not in " ".join(bullets)
    assert "[author]" not in " ".join(bullets)
    assert cache_file.is_file()


def test_blockers_resolve_cron_names_drop_gateway_noise_and_stop_safely(tmp_path):
    blockers_mod = load("blockers")
    home = tmp_path / "home"
    _seed_cron(
        home,
        jobs={
            "c55cc0975f38": "Stale duplicate ask watch",
            "f58038cae3fb": "Coordinate benchmark emails",
        },
        running_execs=[
            ("exec_one", "c55cc0975f38", os.getpid()),
            ("exec_two", "f58038cae3fb", os.getpid()),
        ],
    )
    _seed_state_db(
        home,
        sessions={"s_auto": "Automated digest thread", "s_human": "Interactive coding chat"},
        leases={
            "s_auto": "proc1:turn1:auto=kanban_notice:platform=local",
            "s_human": "proc2:turn2:platform=desktop",
        },
    )
    items = blockers_mod.collect_blockers(
        home,
        progress={
            "state": "draining",
            "waiting_on": [
                "cron:c55cc0975f38; cron:f58038cae3fb; gateway .hermes pid=23468: active=4 units=4 (cron,managed)"
            ],
        },
        force_request={"requested_at": 1.0},
        status={"state": "idle"},
        waiter=None,
    )
    labels = [b["label"] for b in items]
    assert "Stale duplicate ask watch" in labels
    assert "Coordinate benchmark emails" in labels
    assert "Automated digest thread" in labels
    assert "Interactive coding chat" in labels
    # Redundant daemon summary is suppressed when concrete cron items are listed.
    assert not any("pid=" in b["label"] or "units=" in b["label"] for b in items)

    # Automated turn has Stop action; interactive human turn does not get killed.
    by_id = {b["id"]: b for b in items}
    assert by_id["s_auto"]["action"] == "stop"
    assert by_id["s_human"]["action"] is None

    # Stop single cron writes execution .cancel marker.
    res = blockers_mod.act_on_blocker(home, {"action": "stop", "kind": "cron", "id": "c55cc0975f38", "profile": "default"})
    assert res["ok"] and res["stopped"] == 1
    assert (home / "cron" / "cancels" / "exec_one.cancel").is_file()
    assert not (home / "cron" / "cancels" / "exec_two.cancel").is_file()

    # Stop all stops remaining cron AND automated turn, never human turn.
    res_all = blockers_mod.act_on_blocker(home, {"action": "stop_all"})
    assert res_all["ok"] and res_all["stopped"] >= 2
    assert (home / "cron" / "cancels" / "exec_two.cancel").is_file()

    conn = sqlite3.connect(home / "state.db")
    end_rows = dict(conn.execute("SELECT conversation_id, reason FROM session_turn_end_requests").fetchall())
    conn.close()
    assert "s_auto" in end_rows
    assert "s_human" not in end_rows

    # Path traversal in profile is rejected.
    with pytest.raises(ValueError):
        blockers_mod.act_on_blocker(home, {"action": "stop", "kind": "cron", "id": "x", "profile": "../etc"})

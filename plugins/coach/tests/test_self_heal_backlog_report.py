"""Standing report lifecycle: daily rotation, one clock-only schedule per card, never re-scheduled."""
import argparse
import json
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from test_self_heal_one_card import sh, env, NOW


def _day_start(ts):
    return int(ts) // 86400 * 86400


def _archives(env):
    return [a for a in env.board.calls if "archive" in a]


def test_same_day_drains_edit_but_never_reschedule(env, monkeypatch):
    monkeypatch.setattr(sh, "lane_config", lambda: {"backlog_ranked": True})
    sh.retry_deferred(env.store, now=NOW, report_runner=env.board)
    sh.retry_deferred(env.store, now=NOW + 3600, report_runner=env.board)
    assert len(env.board.creates()) == 1
    assert len(env.board.schedules) == 1  # a scheduled card cannot be renewed: no second schedule
    edits = [a for a in env.board.calls if "edit" in a]
    assert len(edits) == 2  # body refreshed once per drain
    assert "archive this card" in edits[-1][-1] and "Never edit the backlog rows" in edits[-1][-1]
    assert sh.cards_created_today("default", NOW) == 0  # excluded from the cap


def test_horizon_is_next_midnight_plus_48h(env):
    assert sh.refresh_backlog_report(env.store, NOW, runner=env.board)["ok"]
    sched = env.board.schedules[0]
    assert "--clock-only" in sched
    assert "--awaiting" in sched and "clock:" in sched
    assert "--awaiting-why" in sched
    assert "drain dead-man alarm: runs only if the self-heal backlog drain stops refreshing it" in sched
    assert sh._iso(_day_start(NOW) + 86400 + 48 * 3600) in sched[1]
    assert env.board.rows()[-1][2] == f"selfheal-backlog:default:{sh._utc_day(NOW)}"


def test_rotation_archives_yesterday_only_after_today_is_scheduled(env):
    first = sh.refresh_backlog_report(env.store, NOW, runner=env.board)["card"]
    nxt = NOW + 86400
    second = sh.refresh_backlog_report(env.store, nxt, runner=env.board)
    assert second["ok"] and second["card"] != first
    assert len(env.board.schedules) == 2
    (arch,) = _archives(env)
    assert first in arch
    calls = env.board.calls
    last_schedule = max(i for i, a in enumerate(calls) if "schedule" in a)
    assert calls.index(arch) > last_schedule
    assert sh.cards_created_today("default", nxt) == 0


def test_failed_create_keeps_yesterday(env):
    first = sh.refresh_backlog_report(env.store, NOW, runner=env.board)["card"]

    def no_create(argv, **kwargs):
        if "create" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="board down")
        return env.board(argv, **kwargs)
    res = sh.refresh_backlog_report(env.store, NOW + 86400, runner=no_create)
    assert not res["ok"]
    assert not _archives(env)
    assert sh._json_state(env.store, sh.BACKLOG_REPORT_STATE)["default"]["card"] == first


def test_failed_schedule_keeps_yesterday_then_retries(env):
    first = sh.refresh_backlog_report(env.store, NOW, runner=env.board)["card"]
    nxt = NOW + 86400

    def no_schedule(argv, **kwargs):
        if "schedule" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="schedule refused")
        return env.board(argv, **kwargs)
    res = sh.refresh_backlog_report(env.store, nxt, runner=no_schedule)
    assert not res["ok"] and not _archives(env)
    res = sh.refresh_backlog_report(env.store, nxt + 3600, runner=env.board)
    assert res["ok"] and res["card"] != first
    assert first in _archives(env)[0]


def test_failed_body_edit_does_not_schedule(env):
    def fail_edit(argv, **kwargs):
        if "edit" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="edit refused")
        return env.board(argv, **kwargs)
    result = sh.refresh_backlog_report(env.store, NOW, runner=fail_edit)
    assert not result["ok"]
    assert not env.board.schedules


def test_closed_by_hand_report_gets_numbered_replacement(env):
    day = sh._utc_day(NOW)
    env.board.add("t_000000ff", key=f"selfheal-backlog:default:{day}", status="done")
    result = sh.refresh_backlog_report(env.store, NOW, runner=env.board)
    assert result["ok"]
    assert result["card"] != "t_000000ff"
    assert env.board.rows()[-1][2] == f"selfheal-backlog:default:{day}:2"
    assert sh.cards_created_today("default", NOW) == 0


@pytest.mark.parametrize("status", ["ready", "scheduled", "triage", "running", "blocked"])
def test_existing_area_report_is_adopted_after_cache_loss(env, status):
    day = sh._utc_day(NOW)
    env.board.add("t_000000ff", key=f"selfheal-backlog:default:{day}:2", status=status)
    result = sh.refresh_backlog_report(env.store, NOW, runner=env.board)
    assert result["ok"] and result["card"] == "t_000000ff"
    assert env.board.creates() == []
    assert env.board.comments[-1][0] == "t_000000ff"
    # Preserve both an existing clock schedule and lane admission holds.
    assert len(env.board.schedules) == (1 if status == "ready" else 0)
    assert sh.refresh_backlog_report(env.store, NOW + 60, runner=env.board)["card"] == "t_000000ff"
    assert env.board.creates() == []
    assert len(env.board.schedules) == (1 if status == "ready" else 0)


def test_adopted_numbered_report_retains_generation_after_closure(env):
    day = sh._utc_day(NOW)
    for gen in range(1, 6):
        key = f"selfheal-backlog:default:{day}" + (f":{gen}" if gen > 1 else "")
        env.board.add(f"t_{gen:08x}", key=key, status="scheduled" if gen == 5 else "done")
    adopted = sh.refresh_backlog_report(env.store, NOW, runner=env.board)
    assert adopted["ok"] and adopted["card"] == "t_00000005"
    from sqlite_fixture import transaction
    with transaction(env.board.db) as con:
        con.execute("UPDATE tasks SET status='done' WHERE id=?", (adopted["card"],))
    replacement = sh.refresh_backlog_report(env.store, NOW + 60, runner=env.board)
    assert replacement["ok"] and replacement["card"] != adopted["card"], replacement
    assert env.board.rows()[-1][2] == f"selfheal-backlog:default:{day}:6"


def test_closed_collision_progress_survives_bounded_retry(env):
    day = sh._utc_day(NOW)
    for gen in range(1, 8):
        key = f"selfheal-backlog:default:{day}" + (f":{gen}" if gen > 1 else "")
        env.board.add(f"t_{gen:08x}", key=key, status="done")
    assert not sh.refresh_backlog_report(env.store, NOW, runner=env.board)["ok"]
    assert not sh.refresh_backlog_report(env.store, NOW + 60, runner=env.board)["ok"]
    replacement = sh.refresh_backlog_report(env.store, NOW + 120, runner=env.board)
    assert replacement["ok"], replacement
    assert env.board.rows()[-1][2] == f"selfheal-backlog:default:{day}:8"


def test_committed_schedule_lost_ack_recovers_retirement(env):
    first = sh.refresh_backlog_report(env.store, NOW, runner=env.board)["card"]

    def lost_ack(argv, **kwargs):
        if "schedule" in argv:
            tid = argv[argv.index("schedule") + 1]
            from sqlite_fixture import transaction
            with transaction(env.board.db) as con:
                con.execute("UPDATE tasks SET status='scheduled' WHERE id=?", (tid,))
            return SimpleNamespace(returncode=1, stdout="", stderr="ack lost after commit")
        return env.board(argv, **kwargs)

    failed = sh.refresh_backlog_report(env.store, NOW + 86400, runner=lost_ack)
    assert not failed["ok"] and not _archives(env)
    recovered = sh.refresh_backlog_report(env.store, NOW + 86460, runner=env.board)
    assert recovered["ok"] and recovered["card"] == failed["card"], recovered
    slot = sh._json_state(env.store, sh.BACKLOG_REPORT_STATE)["default"]
    assert slot["scheduled"] is True and slot["prev"] is None
    assert first in _archives(env)[0]
    assert len(env.board.schedules) == 1  # no redundant schedule after confirmed commit


def test_schedule_parser_lifecycle_retry_rotation(tmp_path, monkeypatch):
    """Create/edit/archive transport is local; schedule uses real argparse + lifecycle.

    In particular, do not mock awaiting validation: the old payload must be refused.
    No dispatcher, remote board, paid model or production card is used.
    """
    pytest.importorskip("hermes_cli.kanban_parser")
    from hermes_cli import kanban_parser, kanban_db as kb
    from hermes_cli.kanban_cli_park import _cli_awaiting, _cli_wake_check
    from hermes_cli.kanban_db_connect import connect
    from agent.delegation_context import kanban_path_is_fenced
    from test_self_heal_one_card import core

    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    db = home / "kanban.db"
    assert kb.kanban_db_path() == db and not kanban_path_is_fenced(db)
    parser = argparse.ArgumentParser()
    kanban_parser.build_parser(parser.add_subparsers(dest="cmd"))
    store = core.Store(home / "coach")
    calls = []
    fail_schedule = False
    now = time.time()

    with closing(connect(db)) as conn:
        def runner(argv, **kwargs):
            nonlocal fail_schedule
            args = parser.parse_args(list(argv[1:]))
            calls.append(list(argv))
            ok = SimpleNamespace(returncode=0, stdout="", stderr="")
            if args.kanban_action == "create":
                tid = f"t_{len(calls):016x}"
                # Fixture-only create seam. The schedule writer sees the migrated real schema.
                conn.execute("INSERT INTO tasks (id,title,body,status,created_by,created_at) "
                             "VALUES (?,?,'','ready','self-heal',?)", (tid, args.title, int(now)))
                return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")
            if args.kanban_action == "schedule":
                if fail_schedule:
                    return SimpleNamespace(returncode=1, stdout="", stderr="injected transport failure")
                wake = _cli_wake_check(args)
                awaiting, wake = _cli_awaiting(args, wake)
                try:
                    accepted = kb.schedule_task(conn, args.task_id, reason=" ".join(args.reason),
                                                awaiting=awaiting, wake_check=wake[0],
                                                check_every=wake[1], clock_only=wake[2])
                except ValueError as exc:
                    return SimpleNamespace(returncode=1, stdout="", stderr=str(exc))
                assert accepted and kb.get_task(conn, args.task_id).status == "scheduled"
            if args.kanban_action == "archive":
                # Retirement is allowed only after the actual successor park has committed.
                successor = sh._json_state(store, sh.BACKLOG_REPORT_STATE)["default"]["card"]
                assert kb.get_task(conn, successor).status == "scheduled"
                conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (argv[argv.index("archive") + 1],))
            return ok

        first = sh.refresh_backlog_report(store, now, runner=runner)
        assert first["ok"], first
        fail_schedule = True
        failed = sh.refresh_backlog_report(store, now + 86400, runner=runner)
        assert not failed["ok"]
        slot = sh._json_state(store, sh.BACKLOG_REPORT_STATE)["default"]
        assert slot["prev"] == first["card"] and slot["scheduled"] is False
        assert kb.get_task(conn, first["card"]).status == "scheduled"
        assert not any("archive" in a for a in calls)
        fail_schedule = False
        recovered = sh.refresh_backlog_report(store, now + 86400 + 60, runner=runner)
        assert recovered["ok"] and recovered["card"] == failed["card"], recovered
        assert kb.get_task(conn, first["card"]).status == "archived"
        slot = sh._json_state(store, sh.BACKLOG_REPORT_STATE)["default"]
        assert slot["scheduled"] is True and slot["prev"] is None
        payloads = [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='scheduled'", (recovered["card"],))]
        assert len(payloads) == 1
        assert payloads[0]["clock_only"] is True
        assert payloads[0]["awaiting"] == {
            "kind": "clock", "why": "drain dead-man alarm: runs only if the self-heal backlog drain stops refreshing it"}

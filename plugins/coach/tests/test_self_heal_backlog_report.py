"""Standing report lifecycle: daily rotation, one clock-only schedule per card, never re-scheduled."""
from types import SimpleNamespace

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
    assert "--clock-only" in env.board.schedules[0]
    assert sh._iso(_day_start(NOW) + 86400 + 48 * 3600) in env.board.schedules[0][1]
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

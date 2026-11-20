"""Standing report lifecycle: successful refresh rearms a clock-only alarm."""
from types import SimpleNamespace

from test_self_heal_one_card import sh, env, NOW


def test_drain_rearms_report_and_excludes_it_from_cap(env, monkeypatch):
    monkeypatch.setattr(sh, "lane_config", lambda: {"backlog_ranked": True})
    sh.retry_deferred(env.store, now=NOW, report_runner=env.board)
    sh.retry_deferred(env.store, now=NOW + 3600, report_runner=env.board)
    assert len(env.board.creates()) == 1
    assert len(env.board.schedules) == 2
    assert sh._iso(NOW + 3600 + 48 * 3600) in env.board.schedules[-1][1]
    assert "--clock-only" in env.board.schedules[-1]
    assert sh.cards_created_today("default", NOW) == 0
    edits = [a for a in env.board.calls if "edit" in a]
    assert len(edits) == 2
    assert "Never edit the backlog rows" in edits[-1][-1]


def test_failed_body_edit_does_not_rearm(env):
    def fail_edit(argv, **kwargs):
        if "edit" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="edit refused")
        return env.board(argv, **kwargs)
    result = sh.refresh_backlog_report(env.store, NOW, runner=fail_edit)
    assert not result["ok"]
    assert not env.board.schedules


def test_closed_report_recreated(env):
    env.board.add("t_000000ff", key="selfheal-backlog:default", status="done")
    result = sh.refresh_backlog_report(env.store, NOW, runner=env.board)
    assert result["ok"]
    assert result["card"] != "t_000000ff"
    assert env.board.rows()[-1][2] == "selfheal-backlog:default:2"
    assert sh.cards_created_today("default", NOW) == 0

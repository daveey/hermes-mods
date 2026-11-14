"""Keep hung-backend attribution bounded and distinct from other timeout owners."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "backend_timeouts_stall_clauses_test",
    Path(__file__).resolve().parents[1] / "backend_timeouts.py",
)
assert spec is not None and spec.loader is not None
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

START = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def event(seconds, kind, conn="remote-a", detail=None):
    return START + timedelta(seconds=seconds), kind, conn, detail


def episodes(events):
    resolved = [(ts, kind, conn) for ts, kind, conn, _ in events
                if kind not in ("dial", "dash_down")]
    return bt.backend_stall_episodes(
        events, resolved, START - timedelta(seconds=300), START + timedelta(seconds=600)
    )


def hang():
    return [event(0, "teardown"), event(1, "dial", detail=800),
            event(34, "dash_down", None, ("4242", "9147"))]


def awake(seconds, path="/a", conn="remote-a"):
    return START + timedelta(seconds=seconds), path, conn


def classify(rows):
    return bt.classify(rows, hang(), START - timedelta(seconds=300),
                       START + timedelta(seconds=600))


def report():
    """A hung remote with enough shared-path leftovers to resemble saturation."""
    return {
        "awake_count": 100,
        "awake_by_conn": {"remote-a": 80, "remote-b": 20},
        "backend_stall": {"count": 50, "conns": ["remote-a"],
                          "by_conn": {"remote-a": 50}},
        "awake_conn_bursts": {
            "remote-a": {"count": 30, "paths": {"/a": 10, "/b": 10, "/c": 10},
                         "burst_by_path": {"/a": 10, "/b": 10, "/c": 10},
                         "burst_buckets": 3, "in_bursts": 30},
        },
        "awake_by_path": {"/a": 30, "/b": 30, "/c": 20, "/other": 20},
        "awake_explained_by_path": {"/a": 20, "/b": 20, "/c": 10},
    }


def test_latest_dial():
    events = [event(0, "teardown"), event(1, "dial", detail=800),
              event(2, "teardown", "remote-b"), event(3, "dial", "remote-b", 800),
              event(34, "dash_down", None, ("1", "9"))]
    assert [episode["conn"] for episode in episodes(events)] == ["remote-b"]


def test_dashboard_max_age():
    # The trial bridges the sequence window, but cannot make a stale dashboard fresh.
    events = hang()[:2] + [event(100, "gate_trial"),
                          event(190, "dash_down", None, ("1", "9"))]
    assert [confirm[1] for confirm in episodes(events)[0]["confirms"]] == ["trial_exceeded"]


def test_gone_max_age():
    events = [event(-200, "gate_down", detail="read ECONNRESET")] + hang()
    assert len(episodes(events)) == 1


def test_next_teardown():
    events = [event(0, "teardown"), event(1, "dial", detail=800),
              event(20, "teardown"), event(34, "dash_down", None, ("1", "9"))]
    assert episodes(events) == []


def test_dial_conn_scope():
    events = [event(0, "teardown"), event(1, "dial", "remote-b", 800),
              event(34, "gate_trial")]
    assert episodes(events) == []


def test_sequence_max_age():
    events = [event(0, "teardown"), event(1, "dial", detail=800), event(200, "gate_trial")]
    assert episodes(events) == []


def test_client_precedence():
    rows = [awake(45, path, conn) for conn in ("remote-a", "remote-b")
            for path in ("/a", "/b", "/c", "/d")]
    result = classify(rows)
    assert result["client_stall"]["count"] == 8
    assert result["backend_stall"]["count"] == 0


def test_no_burst_fallback():
    assert classify([awake(45)])["backend_stall"]["conns"] == ["remote-a"]


def test_burst_not_all():
    rows = [awake(45, path) for path in ("/a", "/b", "/c", "/d")]
    rows += [awake(300 + i * 11) for i in range(15)]
    assert classify(rows)["backend_stall"]["conns"] == ["remote-a"]


def test_kept_exclusion():
    assert classify([awake(45)])["awake_conn_bursts"] == {}


def test_saturation_veto():
    assert bt.saturated_conns(report(), 30) == []


def test_significance():
    result = report()
    result["awake_by_conn"] = {"remote-a": 10, "remote-b": 90}
    assert bt.stall_card_conns(result, 30) == []


def test_path_leftovers():
    assert bt.significant_paths(report(), 10) == ["/other"]


def test_card_episode_conn():
    result = {"backend_stall": {"episodes": [
        {"conn": conn, "teardown": "time", "dials": [], "confirms": []}
        for conn in ("remote-a", "remote-b")
    ]}}
    text = bt._episodes_md(result, "remote-a")
    assert "remote-a" in text
    assert "remote-b" not in text


def test_hung_summary():
    result = report()
    result["backend_stall"]["burst_share"] = {"remote-a": .75}
    assert "Backend stall (hung far-end backend)" in bt._stall_md(result)

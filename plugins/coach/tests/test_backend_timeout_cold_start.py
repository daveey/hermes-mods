"""backend_timeouts: timeouts on the Desktop's own backend right after it (re)started are a cold start, and that
conn has no link to saturate.

2026-11-13 13:58 PST a core update restarted the MacBook's gateway. The Desktop logged `[backend] Hermes gateway is
ready` at 13:58:49.990 and released its polls. The supervised gateway publishes READY and only then mounts its
dashboard API in the background (gateway/run_bootstrap.py `warm_gateway_dashboard`), so the first `/api` request
entered the app at 13:58:59.3 and every poll issued in between waited past its 4-8 s timeout: 11 timeouts across 6
paths in 3 shared 2-second buckets. The detector filed "Desktop link saturation on conn=local" and asked for the
tunnel's Send-Q and per-path byte attribution. `conn=local` has no tunnel. All 29 local timeouts in that day's
retained logs sat in the minute of a READY line (6 of 10 restarts).

The fixture is that real desktop.log slice: the READY line and the local timeout lines (stack lines dropped).
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "desktop_local_cold_start_2026-11-13.txt"
spec = importlib.util.spec_from_file_location("backend_timeouts_cold_start_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

_owners = importlib.util.spec_from_file_location("backend_timeouts_owners_helpers_cold",
                                                 Path(__file__).with_name("test_backend_timeout_path_owners.py"))
helpers = importlib.util.module_from_spec(_owners)
_owners.loader.exec_module(helpers)
Board = helpers.Board

END = datetime(2026, 11, 13, 22, 10, tzinfo=timezone.utc)  # 14:10 PST, the pass that filed the card
READY = datetime(2026, 11, 13, 21, 58, 49, tzinfo=timezone.utc)
PST = timezone(timedelta(hours=-8))
BOARD = "/api/plugins/kanban/board"
BOARDS = "/api/plugins/kanban/boards"
HUB_SYNC = "/api/plugins/hub-sync/status"
LABELS = "/api/plugins/kanban/session-labels"


@pytest.fixture(autouse=True)
def isolate_power(monkeypatch):
    monkeypatch.setattr(bt, "probe_power", lambda now: {"status": "ok", "intervals": []}, raising=False)


def install(home, text):
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "desktop.log").write_text(text)


def report(home, capsys, board=None, now=END, dry=False):
    argv = ["--home", str(home), "--host", "laptop", "--json"]
    if dry:
        argv.append("--dry-run")
    assert bt.main(argv, runner=board, now=now, probe=lambda: {"loadavg": [1.0, 1.0, 1.0], "backend_ps": []}) == 0
    return json.loads(capsys.readouterr().out)


def keys(board):
    return [c[0][c[0].index("--idempotency-key") + 1] for c in board.creates()]


def stamp(t):
    return t.astimezone(PST).strftime("%Y-%m-%d %H:%M:%S,000 -08:00")


def timeout(t, path, conn="local"):
    return (f"{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} "
            "Error: Timed out connecting to Hermes backend after 4000ms")


def ready(t):
    return f"{stamp(t)} [hermes] [backend] Hermes gateway is ready"


def burst(t0, conn="local", seconds=3):
    out = []
    for s in range(seconds):
        t = t0 + timedelta(seconds=2 * s)
        out += [timeout(t, BOARD, conn), timeout(t, BOARDS, conn), timeout(t, HUB_SYNC, conn),
                timeout(t, LABELS, conn)]
    return out


def lone_remote(n=37, conn="mac-mini"):
    """One slow path on a remote conn, a timeout every 30 s: over the threshold, never a shared burst."""
    return [timeout(END - timedelta(minutes=22) + timedelta(seconds=30 * i), HUB_SYNC, conn) for i in range(n)]


# --- the real incident -------------------------------------------------------------------------------------------

def test_real_restart_is_cold_start_not_local_link_saturation(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text() + "\n".join(lone_remote()) + "\n")
    out = report(tmp_path, capsys, dry=True)
    assert out["awake_by_conn"] == {"mac-mini": 37, "local": 10, "?": 1}  # the raw count is unchanged
    assert out["over_threshold"] is True
    assert out["saturated_conns"] == []
    assert "local" not in out["awake_conn_bursts"] and "?" not in out["awake_conn_bursts"]
    assert out["backend_cold_start"] == {
        "count": 11, "by_conn": {"local": 10, "?": 1},
        "by_path": {BOARDS: 4, HUB_SYNC: 2, BOARD: 2, "/api/plugins/hub-sync/tasks": 1, "[fleet-roster]": 1,
                    "/api/plugins/hub-sync/sessions": 1},
        "readies": ["2026-11-13 21:58:49+00:00"]}
    assert sum(out["awake_explained_by_path"].values()) == 11


def test_real_restart_files_no_conn_card_and_the_incident_card_says_cold_start(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text() + "\n".join(lone_remote()) + "\n")
    board = Board()
    report(tmp_path, capsys, board=board)
    assert not [k for k in keys(board) if ":conn:" in k], keys(board)
    bodies = "\n".join(body or "" for _argv, body in board.creates())
    assert "Backend cold start: 11 timeouts" in bodies and "Hermes gateway is ready" in bodies
    assert "2026-11-13 21:58:49+00:00" in bodies
    assert "Link/connection saturation suspected" not in bodies


# --- the boundaries ------------------------------------------------------------------------------------------------

def test_local_timeouts_past_the_cold_start_window_are_not_cold_start(tmp_path, capsys):
    late = READY + timedelta(seconds=bt.COLD_START_S + 1)
    install(tmp_path, "\n".join([ready(READY), timeout(READY + timedelta(seconds=bt.COLD_START_S), BOARD),
                                 timeout(late, BOARD), timeout(late, BOARDS)] + lone_remote()) + "\n")
    out = report(tmp_path, capsys, dry=True)
    assert out["backend_cold_start"]["count"] == 1 and out["backend_cold_start"]["by_path"] == {BOARD: 1}
    assert out["awake_conn_bursts"]["local"]["count"] == 2


def test_local_timeouts_before_ready_are_not_cold_start(tmp_path, capsys):
    install(tmp_path, "\n".join([timeout(READY - timedelta(seconds=5), BOARD), ready(READY)] + lone_remote()) + "\n")
    out = report(tmp_path, capsys, dry=True)
    assert out["backend_cold_start"] == {"count": 0, "by_conn": {}, "by_path": {}, "readies": []}


def test_remote_timeouts_after_a_local_ready_are_not_cold_start_and_stay_saturation(tmp_path, capsys):
    """READY is about the Desktop's own backend: a remote conn's bursts in the same seconds are still its link."""
    lines = [ready(READY)] + burst(READY + timedelta(seconds=2), conn="mac-mini", seconds=10)
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, dry=True)
    assert out["backend_cold_start"]["count"] == 0
    assert out["saturated_conns"] == ["mac-mini"]


def test_local_bursts_with_no_ready_line_are_never_link_saturation(tmp_path, capsys):
    """The Desktop's own backend has no tunnel: its bursts stay unexplained (the incident and path cards time the
    backend) and file no connection card."""
    lines = burst(END - timedelta(minutes=20), seconds=6) + burst(END - timedelta(minutes=10), seconds=6)
    install(tmp_path, "\n".join(lines) + "\n")
    board = Board()
    out = report(tmp_path, capsys, board=board)
    assert out["over_threshold"] is True
    assert out["backend_cold_start"]["count"] == 0
    assert out["awake_conn_bursts"]["local"]["count"] == 48  # still reported as bursts
    assert out["saturated_conns"] == []
    assert not [k for k in keys(board) if ":conn:" in k], keys(board)
    assert "backend-timeouts:laptop:2026-11-13" in keys(board)  # the incident card owns them

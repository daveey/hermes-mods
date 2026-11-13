"""backend_timeouts: a down-gate fail-fast line does not count toward the incident threshold.

While a remote conn is gated down, the Desktop answers each request at once with
`Remote backend "conn:<c>::default" is down (<last error>, Ns ago); ... Failing fast instead of waiting for a timeout.`
When the conn's last error happened to be a timeout, that line quotes "Timed out connecting to Hermes backend", so
every poll during the outage read as one more timeout toward the threshold. When the last error was anything else
(`read ECONNRESET`, `connect ECONNREFUSED`) the same lines were never counted. They stay in the raw and awake counts
(and in backend_down); only the threshold leaves them out.

2026-11-13 12:27-12:36 PST (20:27-20:36Z): a core update restarted the far end's always-on dashboard, which took
about a minute to serve again. The Desktop logged 9 real timeouts in 30 min (7 on the hub conn, 2 down-gate trial
deadlines on the restarted conn) and 22 fail-fast lines quoting the second kind. The detector reported 31 (threshold
30), filed "link saturation on conn=mac-mini" and a per-path owner card for hub-sync/status.

The fixture is that real desktop.log slice (timeout, `[slow-link]`, `[ssh]`, `[remote-down]` and dispatch-probe lines;
hostnames, the tailnet IP, the user name and query strings scrubbed).
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "desktop_fail_fast_echo_2026-11-13.txt"
spec = importlib.util.spec_from_file_location("backend_timeouts_fail_fast_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

_owners = importlib.util.spec_from_file_location("backend_timeouts_owners_helpers_fail_fast",
                                                 Path(__file__).with_name("test_backend_timeout_path_owners.py"))
helpers = importlib.util.module_from_spec(_owners)
_owners.loader.exec_module(helpers)
Board = helpers.Board

END = datetime(2026, 11, 13, 20, 53, 39, tzinfo=timezone.utc)  # 12:53:39 PST, the pass that filed the cards
PST = timezone(timedelta(hours=-8))
HUB_SYNC = "/api/plugins/hub-sync/status"
BOARD = "/api/plugins/kanban/board"


@pytest.fixture(autouse=True)
def isolate_power(monkeypatch):
    monkeypatch.setattr(bt, "probe_power", lambda now: {"status": "ok", "intervals": []}, raising=False)


def install(home, text):
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "desktop.log").write_text(text)


def run(home, board=None, now=END, window=30, dry=False):
    argv = ["--home", str(home), "--host", "laptop", "--json", "--window", str(window)]
    if dry:
        argv.append("--dry-run")
    return bt.main(argv, runner=board, now=now, probe=lambda: {"loadavg": [1.0, 1.0, 1.0], "backend_ps": []})


def report(home, capsys, **kw):
    assert run(home, **kw) == 0
    return json.loads(capsys.readouterr().out)


def stamp(t):
    return t.astimezone(PST).strftime("%Y-%m-%d %H:%M:%S,000 -08:00")


def timeout(t, path, conn="hermes-hub"):
    return (f"{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} "
            "Error: Timed out connecting to Hermes backend after 4000ms")


def fail_fast(t, path, conn="mac-mini", ago=12):
    return (f"{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} Remote backend \"conn:{conn}::default\" is down "
            f"(Timed out connecting to Hermes backend after 20000ms (down-gate trial deadline), {ago}s ago); "
            "next try in 3s. Failing fast instead of waiting for a timeout.")


# --- the real incident -------------------------------------------------------------------------------------------

def test_real_backend_restart_is_nine_timeouts_toward_the_threshold_not_thirty_one(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text())
    out = report(tmp_path, capsys, dry=True)
    assert out["count"] == 31 and out["awake_count"] == 31  # raw counts unchanged
    ff = out["fail_fast"]
    assert ff["count"] == 22 and ff["awake_count"] == 22 and ff["by_conn"] == {"mac-mini": 22}
    assert ff["by_path"][HUB_SYNC] == 9
    # 31 - 22 = 9 real timeouts (7 on the hub conn, 2 gate-trial deadlines); before: 31 >= 30 filed cards
    assert out["over_threshold"] is False and out["cards"] == []


def test_real_backend_restart_files_no_card(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text())
    board = Board()
    out = report(tmp_path, capsys, board=board)
    assert board.creates() == [] and board.comments() == [] and out["cards"] == []


# --- what still counts ---------------------------------------------------------------------------------------------

def test_real_timeouts_during_an_outage_still_file_the_incident(tmp_path, capsys):
    """A conn that stays down keeps timing out its trial requests: those are real and still reach the threshold,
    whatever number of fail-fast lines sit between them."""
    lines = []
    for i in range(32):
        t = END - timedelta(minutes=29) + timedelta(seconds=40 * i)
        lines.append(timeout(t, HUB_SYNC, "mac-mini"))
        lines += [fail_fast(t + timedelta(seconds=s), BOARD) for s in (5, 10, 15)]
    install(tmp_path, "\n".join(lines) + "\n")
    board = Board()
    out = report(tmp_path, capsys, board=board)
    assert out["count"] == 128 and out["fail_fast"]["awake_count"] == 96
    assert out["over_threshold"] is True and len(board.creates()) >= 1


def test_fail_fast_lines_alone_never_reach_the_threshold(tmp_path, capsys):
    lines = [fail_fast(END - timedelta(minutes=20) + timedelta(seconds=s), HUB_SYNC, ago=s) for s in range(60)]
    install(tmp_path, timeout(END - timedelta(minutes=21), HUB_SYNC, "mac-mini") + "\n" + "\n".join(lines) + "\n")
    board = Board()
    out = report(tmp_path, capsys, board=board)
    assert out["count"] == 61 and out["fail_fast"]["count"] == 60
    assert out["over_threshold"] is False and board.creates() == []


def test_fail_fast_outside_the_window_is_not_reported(tmp_path, capsys):
    install(tmp_path, fail_fast(END - timedelta(minutes=45), HUB_SYNC) + "\n"
            + timeout(END - timedelta(minutes=5), HUB_SYNC) + "\n")
    out = report(tmp_path, capsys, dry=True)
    assert out["count"] == 1 and out["fail_fast"] == {"count": 0, "awake_count": 0, "by_conn": {}, "by_path": {}}

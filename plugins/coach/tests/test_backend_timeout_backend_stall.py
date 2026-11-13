"""backend_timeouts: a hung far-end backend (fast SSH re-dial, then the backend still silent) is backend_stall, not link
saturation.

2026-11-13 12:03-12:13 PST (20:03-20:13Z) the detector filed "Desktop link saturation on conn=hermes-hub" (265
timeouts) and asked for per-path byte attribution on the hub. The link was healthy: /api/health took 0.12-0.33 s through
the tunnels, the hub link carried about 44 KB/s and Send-Q was about 0. A blocking `py-spy record` had stopped the hub's
always-on serve from 12:02:53 to 12:19:51 PST. The Desktop log shows the signature twice: `[slow-link] ... treating it
as down`, `[ssh] teardown conn:hermes-hub`, a re-dial in under a second or two (`ssh dial +724 ms`, `+2091 ms`), then
`always-on dashboard unavailable (pid 3875280 not answering on port 9147)` and `[remote-down] ... trial exceeded` on the
fresh tunnel. A saturated link would have slowed the re-dial too.

The fixture is that real desktop.log slice (12:00-12:20 PST; timeout, `[slow-link]`, `[ssh]`, `[ssh-lifecycle]`,
`[remote-down]`, `ssh dial` and `dispatch probe slow` lines; the hub address and host names scrubbed).
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "desktop_backend_stall_2026-11-13.txt"
spec = importlib.util.spec_from_file_location("backend_timeouts_backend_stall_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

_owners = importlib.util.spec_from_file_location("backend_timeouts_owners_helpers_hung",
                                                 Path(__file__).with_name("test_backend_timeout_path_owners.py"))
helpers = importlib.util.module_from_spec(_owners)
_owners.loader.exec_module(helpers)
Board = helpers.Board

END = datetime(2026, 11, 13, 20, 20, tzinfo=timezone.utc)  # 12:20 PST
PST = timezone(timedelta(hours=-8))
BOARD = "/api/plugins/kanban/board"
BOARDS = "/api/plugins/kanban/boards"
HUB_SYNC = "/api/plugins/hub-sync/status"
LABELS = "/api/plugins/kanban/session-labels"
KEY = "backend-timeouts:laptop:backend-stall:hermes-hub"


@pytest.fixture(autouse=True)
def isolate_power(monkeypatch):
    monkeypatch.setattr(bt, "probe_power", lambda now: {"status": "ok", "intervals": []}, raising=False)


def install(home, text):
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "desktop.log").write_text(text)


def report(home, capsys, board=None, now=END, window=20, dry=False):
    argv = ["--home", str(home), "--host", "laptop", "--json", "--window", str(window)]
    if dry:
        argv.append("--dry-run")
    assert bt.main(argv, runner=board, now=now, probe=lambda: {"loadavg": [1.0, 1.0, 1.0], "backend_ps": []}) == 0
    return json.loads(capsys.readouterr().out)


def keys(board):
    return [c[0][c[0].index("--idempotency-key") + 1] for c in board.creates()]


def stamp(t):
    return t.astimezone(PST).strftime("%Y-%m-%d %H:%M:%S,000 -08:00")


def timeout(t, path, conn="hermes-hub"):
    return (f"{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} "
            "Error: Timed out connecting to Hermes backend after 4000ms")


def burst(t0, conn="hermes-hub", seconds=12):
    out = []
    for s in range(seconds):
        t = t0 + timedelta(seconds=s)
        out += [timeout(t, BOARD, conn), timeout(t, BOARDS, conn), timeout(t, HUB_SYNC, conn), timeout(t, LABELS, conn)]
    return out


def teardown(t, conn="hermes-hub"):
    return f"{stamp(t)} [hermes] [ssh] teardown conn:{conn}::default (revalidate): release"


def dial(t, ms, conn="hermes-hub"):
    return f"{stamp(t)} [hermes] [remote-timing] conn:{conn}::default ssh dial +{ms} ms"


def not_answering(t, pid=4242):
    return (f"{stamp(t)} [hermes] [ssh-lifecycle] always-on dashboard unavailable (pid {pid} not answering on port "
            "9147) after 20000 ms; falling back to an owned backend")


def trial(t, conn="hermes-hub"):
    return f"{stamp(t)} [hermes] [remote-down] conn:{conn}::default trial exceeded 20000 ms; slot freed, backoff escalated"


def marked(t, reason, conn="hermes-hub"):
    return f"{stamp(t)} [hermes] [remote-down] conn:{conn}::default marked down ({reason}); failing fast until it answers"


def hang(t, ms=800, conn="hermes-hub"):
    """teardown, a fast re-dial, then the dashboard not answering on the fresh tunnel."""
    return [teardown(t, conn), dial(t + timedelta(seconds=1), ms, conn), not_answering(t + timedelta(seconds=34))]


def write(home, lines):
    install(home, "\n".join(sorted(lines)) + "\n")


# --- the real incident -------------------------------------------------------------------------------------------

def test_real_hung_hub_backend_is_backend_stall_not_saturation(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text())
    out = report(tmp_path, capsys, dry=True)
    assert out["awake_by_conn"] == {"hermes-hub": 265, "local": 4, "?": 3}  # raw counts unchanged
    # current main called this "link saturation on conn=hermes-hub"
    assert out["saturated_conns"] == []
    bs = out["backend_stall"]
    assert bs["conns"] == ["hermes-hub"] and bs["burst_share"]["hermes-hub"] > 0.9
    assert bs["by_conn"] == {"hermes-hub": 242} and bs["count"] == 242
    assert [e["teardown"] for e in bs["episodes"]] == ["2026-11-13 20:04:38+00:00", "2026-11-13 20:11:14+00:00"]
    assert [[d["ms"] for d in e["dials"]] for e in bs["episodes"]] == [[724], [2091, 2012]]
    kinds = [c["kind"] for e in bs["episodes"] for c in e["confirms"]]
    assert kinds.count("dashboard_not_answering") == 3 and kinds.count("trial_exceeded") == 4
    assert {c["detail"] for e in bs["episodes"] for c in e["confirms"] if c["detail"]} == {"pid 3875280 port 9147"}
    assert out["backend_stall_conns"] == ["hermes-hub"]
    assert out["client_stall"]["count"] == 0 and out["uplink_stall"] is False
    assert bt.residual_count(out) < out["threshold"]


def test_real_hung_hub_backend_files_one_hung_backend_card(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text())
    board = Board()
    out = report(tmp_path, capsys, board=board)
    assert keys(board) == [KEY], keys(board)  # no saturation card, no generic card, no victim path cards
    (argv, body), = board.creates()
    assert argv[-1].startswith("Desktop hung backend on conn=hermes-hub: 265 timeouts in 20 min")
    assert "event loop stalled" in body and "loop-watch: event loop stalled" in body
    assert "COMMAND=.*py-spy" in body and "journalctl" in body
    assert "loop-watch.jsonl" in body and "loop_stacks" in body and "3875280" in body
    assert "+724 ms" in body and "+2091 ms" in body
    assert "tcpdump" not in body and "Attribute bytes" not in body
    assert "Link/connection saturation suspected" not in body
    assert out["cards"][0]["kind"] == "backend_stall" and out["cards"][0]["action"] == "created"

    # the next pass within the hour reuses it silently; after an hour it comments on the same card
    report(tmp_path, capsys, board=board, now=END + timedelta(minutes=5), window=25)
    assert len(board.creates()) == 1 and board.comments() == []
    report(tmp_path, capsys, board=board, now=END + timedelta(hours=1, minutes=5), window=85)
    assert len(board.creates()) == 1
    assert [c[0][3] for c in board.comments()] == [board.keys[KEY]]

    # once it closes, a recurrence files :2
    board.tasks[board.keys[KEY]]["task"]["status"] = "done"
    report(tmp_path, capsys, board=board, now=END + timedelta(hours=2, minutes=10), window=150)
    assert keys(board)[-1] == KEY + ":2"


# --- the rule's edges ----------------------------------------------------------------------------------------------

NOW = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)


def test_bursts_with_no_hang_lines_are_still_saturation(tmp_path, capsys):
    write(tmp_path, burst(NOW - timedelta(minutes=20)) + burst(NOW - timedelta(minutes=10)))
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["saturated_conns"] == ["hermes-hub"]
    assert out["backend_stall"]["count"] == 0 and out["backend_stall"]["conns"] == []


def test_hang_lines_absorb_bursts_within_a_minute(tmp_path, capsys):
    a, b = NOW - timedelta(minutes=20), NOW - timedelta(minutes=10)
    write(tmp_path, burst(a) + burst(b) + hang(a - timedelta(seconds=30)) + hang(b - timedelta(seconds=30)))
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["saturated_conns"] == []
    assert out["backend_stall"]["by_conn"] == {"hermes-hub": 96} and out["backend_stall"]["conns"] == ["hermes-hub"]
    assert out["backend_stall_conns"] == ["hermes-hub"]


def test_slow_redial_is_not_a_hung_backend(tmp_path, capsys):
    """A saturated link slows the SSH handshake too: a +8 s re-dial proves nothing about the far end."""
    a, b = NOW - timedelta(minutes=20), NOW - timedelta(minutes=10)
    write(tmp_path, burst(a) + burst(b) + hang(a - timedelta(seconds=30), ms=8000) + hang(b - timedelta(seconds=30), ms=8000))
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["backend_stall"]["count"] == 0
    assert out["saturated_conns"] == ["hermes-hub"]


def test_fast_redial_that_answers_is_not_a_hung_backend(tmp_path, capsys):
    a, b = NOW - timedelta(minutes=20), NOW - timedelta(minutes=10)
    lines = burst(a) + burst(b)
    for t in (a, b):
        lines += [teardown(t - timedelta(seconds=30)), dial(t - timedelta(seconds=29), 700)]
    write(tmp_path, lines)
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["backend_stall"]["count"] == 0 and out["saturated_conns"] == ["hermes-hub"]


def test_trial_exceeded_after_a_fast_redial_confirms_the_hang(tmp_path, capsys):
    a, b = NOW - timedelta(minutes=20), NOW - timedelta(minutes=10)
    lines = burst(a) + burst(b)
    for t in (a, b):
        lines += [teardown(t - timedelta(seconds=30)), dial(t - timedelta(seconds=29), 700), trial(t - timedelta(seconds=5))]
    write(tmp_path, lines)
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["saturated_conns"] == [] and out["backend_stall"]["conns"] == ["hermes-hub"]


def test_closed_connection_is_a_restart_not_a_hang(tmp_path, capsys):
    """`marked down (read ECONNRESET)`: the far end went away (a restart), reported as backend_down, never as a hang
    (2026-11-13 12:31 PST: a core update restarted the mini's serve with the same re-dial lines)."""
    a, b = NOW - timedelta(minutes=20), NOW - timedelta(minutes=10)
    lines = burst(a) + burst(b)
    for t in (a, b):
        lines += [marked(t - timedelta(seconds=31), "read ECONNRESET")] + hang(t - timedelta(seconds=30))
    write(tmp_path, lines)
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["backend_stall"]["count"] == 0 and out["backend_stall"]["episodes"] == []


def test_another_conns_hang_does_not_absorb_this_conns_bursts(tmp_path, capsys):
    a, b = NOW - timedelta(minutes=20), NOW - timedelta(minutes=10)
    write(tmp_path, burst(a) + burst(b) + hang(a - timedelta(seconds=30), conn="mac-mini")
          + hang(b - timedelta(seconds=30), conn="mac-mini"))
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["saturated_conns"] == ["hermes-hub"]
    assert out["backend_stall"]["count"] == 0
    assert [e["conn"] for e in out["backend_stall"]["episodes"]] == ["mac-mini", "mac-mini"]


def test_minority_hang_leaves_saturation_standing(tmp_path, capsys):
    """One hang beside three far-away bursts: under half the conn's burst timeouts are backend_stall."""
    ts = [NOW - timedelta(minutes=m) for m in (25, 20, 15, 10)]
    write(tmp_path, [x for t in ts for x in burst(t)] + hang(ts[0] - timedelta(seconds=30)))
    out = report(tmp_path, capsys, now=NOW, window=30, dry=True)
    assert out["backend_stall"]["by_conn"] == {"hermes-hub": 48}
    assert out["backend_stall"]["conns"] == [] and out["backend_stall"]["burst_share"] == {"hermes-hub": 0.25}
    assert out["saturated_conns"] == ["hermes-hub"]


def test_dial_and_dashboard_lines_parse_and_are_not_slow_link_ports():
    t = datetime(2026, 11, 13, 20, 4, 39, tzinfo=timezone.utc)
    assert bt.link_event(dial(t, 724) + "\n", t) == (t, "dial", "hermes-hub", 724)
    assert bt.link_event(not_answering(t, 3875280), t) == (t, "dash_down", None, ("3875280", "9147"))
    waiting = (f"{stamp(t)} [hermes] [ssh-lifecycle] always-on dashboard pid 64883 not answering on port 9147; "
               "waiting for its supervisor restart")
    assert bt.link_event(waiting, t) is None
    resolved, unmapped = bt.resolve_link_events([bt.link_event(dial(t, 724), t),
                                                 bt.link_event(not_answering(t), t)])
    assert resolved == [] and unmapped == 0

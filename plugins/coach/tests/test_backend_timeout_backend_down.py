"""backend_timeouts: timeouts logged while the Desktop's down gate holds a conn down are a backend outage, not
link saturation.

2026-11-13 12:31 PST (20:31Z) a core update restarted the backend on the mini. The Desktop saw `read ECONNRESET`,
marked conn=mac-mini down and failed every request fast for 61 s, each with a line quoting the trial timeout
("is down (Timed out connecting to Hermes backend after 20000ms (down-gate trial deadline), 27s ago)"). Those 24
lines (5 already a reconnect cascade) spread over 7 paths in shared 2-second buckets, so the detector filed "Desktop
link saturation on conn=mac-mini" and asked for per-path byte attribution. The link was healthy the whole time
(/api/health 0.12 s through a fresh tunnel, 4 ms on the mini's loopback, 80-160 dispatches a minute).

The fixture is that real desktop.log slice (12:26-12:40 PST; timeout, `[slow-link]`, `[ssh]`, `[remote-down]` and
fail-fast lines; hostnames and the user name scrubbed).
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "desktop_backend_restart_2026-11-13.txt"
spec = importlib.util.spec_from_file_location("backend_timeouts_backend_down_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

_owners = importlib.util.spec_from_file_location("backend_timeouts_owners_helpers_down",
                                                 Path(__file__).with_name("test_backend_timeout_path_owners.py"))
helpers = importlib.util.module_from_spec(_owners)
_owners.loader.exec_module(helpers)
Board = helpers.Board

END = datetime(2026, 11, 13, 20, 52, tzinfo=timezone.utc)  # 12:52 PST, the pass that filed the card
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


def report(home, capsys, board=None, now=END, window=30, dry=False):
    argv = ["--home", str(home), "--host", "laptop", "--json", "--window", str(window)]
    if dry:
        argv.append("--dry-run")
    assert bt.main(argv, runner=board, now=now, probe=lambda: {"loadavg": [1.0, 1.0, 1.0], "backend_ps": []}) == 0
    return json.loads(capsys.readouterr().out)


def keys(board):
    return [c[0][c[0].index("--idempotency-key") + 1] for c in board.creates()]


def stamp(t):
    return t.astimezone(PST).strftime("%Y-%m-%d %H:%M:%S,000 -08:00")


def timeout(t, path, conn="mac-mini"):
    return (f"{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} "
            "Error: Timed out connecting to Hermes backend after 4000ms")


def fail_fast(t, path, conn="mac-mini", ago=27):
    return (f'{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} Remote backend "conn:{conn}::default" is down '
            f"(Timed out connecting to Hermes backend after 20000ms (down-gate trial deadline), {ago}s ago); "
            "reconnect in progress. Failing fast instead of waiting for a timeout.")


def gate(t, what, conn="mac-mini"):
    return f"{stamp(t)} [hermes] [remote-down] conn:{conn}::default {what}"


MARKED = "marked down (read ECONNRESET); failing fast until it answers"
TRIAL = "trial exceeded 20000 ms; slot freed, backoff escalated"
ANSWERED = "answered again; gate open"


def burst(t0, conn="mac-mini", seconds=12, line=timeout):
    out = []
    for s in range(seconds):
        t = t0 + timedelta(seconds=s)
        out += [line(t, BOARD, conn), line(t, BOARDS, conn), line(t, HUB_SYNC, conn), line(t, LABELS, conn)]
    return out


# --- the real incident -------------------------------------------------------------------------------------------

def test_real_backend_restart_is_backend_down_not_mini_saturation(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text())
    out = report(tmp_path, capsys, dry=True)
    assert out["awake_by_conn"] == {"mac-mini": 24, "hermes-hub": 7}  # the raw count is unchanged
    assert out["saturated_conns"] == []
    assert "mac-mini" not in out["awake_conn_bursts"]
    assert out["backend_down"] == {
        "count": 24, "by_conn": {"mac-mini": 24},
        "episodes": [{"conn": "mac-mini", "start": "2026-11-13 20:31:19+00:00", "end": "2026-11-13 20:32:20+00:00",
                      "seconds": 61.0, "answered": True, "reason": "read ECONNRESET"}]}
    assert out["reconnect_cascade"]["by_conn"] == {"mac-mini": 5}  # still reported, set aside once
    assert sum(out["awake_explained_by_path"].values()) == 24


def test_real_backend_restart_files_no_conn_card_and_no_victim_path_card(tmp_path, capsys, monkeypatch):
    install(tmp_path, FIXTURE.read_text())
    board = Board()
    # At the default threshold this window files nothing: 22 of its 31 lines are fail-fast echoes
    # (test_backend_timeout_fail_fast.py). With the threshold at its 9 real timeouts the incident card still says so.
    report(tmp_path, capsys, board=board)
    assert keys(board) == []
    monkeypatch.setenv("COACH_BACKEND_TIMEOUTS_THRESHOLD", "9")
    report(tmp_path, capsys, board=board)
    assert keys(board) == ["backend-timeouts:laptop:2026-11-13"], keys(board)  # the incident card only
    (argv, body), = board.creates()
    assert "Backend down: 24 timeouts" in body and "conn=mac-mini" in body and "read ECONNRESET" in body
    assert "for 61.0 s" in body
    assert "Link/connection saturation suspected" not in body


# --- what stays saturation -----------------------------------------------------------------------------------------

def test_bursts_with_no_gate_line_are_still_saturation(tmp_path, capsys):
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    lines = burst(now - timedelta(minutes=20)) + burst(now - timedelta(minutes=10))
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, now=now, dry=True)
    assert out["saturated_conns"] == ["mac-mini"]
    assert out["backend_down"] == {"count": 0, "by_conn": {}, "episodes": []}


def test_bursts_after_the_gate_reopened_are_still_saturation(tmp_path, capsys):
    """A down episode only absorbs its own seconds: real bursts minutes after `answered again` still count."""
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    down = now - timedelta(minutes=25)
    lines = [gate(down, MARKED)] + burst(down + timedelta(seconds=1), seconds=5, line=fail_fast)
    lines += [gate(down + timedelta(seconds=10), ANSWERED)]
    lines += burst(now - timedelta(minutes=20)) + burst(now - timedelta(minutes=10))
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, now=now, dry=True)
    assert out["backend_down"]["count"] == 20 and out["backend_down"]["episodes"][0]["seconds"] == 10.0
    assert out["saturated_conns"] == ["mac-mini"]
    assert out["awake_conn_bursts"]["mac-mini"]["count"] == 96


def test_another_conns_gate_does_not_absorb_this_conns_bursts(tmp_path, capsys):
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    lines = [gate(now - timedelta(minutes=21), MARKED, "hermes-hub")]
    lines += burst(now - timedelta(minutes=20)) + burst(now - timedelta(minutes=10))
    lines += [gate(now - timedelta(minutes=1), ANSWERED, "hermes-hub")]
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, now=now, dry=True)
    assert out["saturated_conns"] == ["mac-mini"]
    assert out["backend_down"]["count"] == 0
    assert [e["conn"] for e in out["backend_down"]["episodes"]] == ["hermes-hub"]


# --- episode edges ---------------------------------------------------------------------------------------------------

def test_episode_with_no_answer_ends_a_minute_after_its_last_gate_line(tmp_path, capsys):
    """A Desktop that quit while the conn was down logs no `answered again`; later timeouts are not its outage."""
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    down = now - timedelta(minutes=25)
    lines = [gate(down, MARKED), gate(down + timedelta(seconds=24), TRIAL)]
    lines += burst(down + timedelta(seconds=30), seconds=5, line=fail_fast)   # inside: 24 s + 60 s
    lines += burst(now - timedelta(minutes=15)) + burst(now - timedelta(minutes=5))  # long after
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, now=now, dry=True)
    (ep,) = out["backend_down"]["episodes"]
    assert ep["seconds"] == 84.0 and ep["answered"] is False
    assert out["backend_down"]["count"] == 20
    assert out["saturated_conns"] == ["mac-mini"]


def test_answer_without_a_retained_marked_down_line_covers_the_lines_before_it(tmp_path, capsys):
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    lines = burst(now - timedelta(minutes=20), seconds=5, line=fail_fast)
    lines += burst(now - timedelta(minutes=19), seconds=5, line=fail_fast)
    lines += [gate(now - timedelta(minutes=18), ANSWERED)]
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, now=now, dry=True)
    assert out["backend_down"]["count"] == 40 and out["saturated_conns"] == []
    assert out["backend_down"]["episodes"][0]["reason"] is None


def test_timeout_that_closed_the_gate_is_part_of_the_episode():
    """The request whose timeout marks the conn down is logged a moment before the `[remote-down]` line."""
    t0 = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
    awake = [(t0 - timedelta(seconds=1), HUB_SYNC, "mac-mini"), (t0 - timedelta(seconds=5), BOARD, "mac-mini")]
    events = [(t0, "gate_down", "mac-mini", "Timed out"), (t0 + timedelta(seconds=30), "gate_up", "mac-mini", None)]
    cls = bt.classify(awake, events, t0 - timedelta(minutes=10), t0 + timedelta(minutes=10))
    assert cls["backend_down"]["count"] == 1
    assert cls["explained_by_path"] == {HUB_SYNC: 1}


# --- line parsing ------------------------------------------------------------------------------------------------------

def test_gate_lines_parse_and_are_not_slow_link_ports():
    t = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
    evs = [bt.link_event(gate(t, what), t) for what in (MARKED, TRIAL, ANSWERED)]
    assert evs == [(t, "gate_down", "mac-mini", "read ECONNRESET"), (t, "gate_trial", "mac-mini", None),
                   (t, "gate_up", "mac-mini", None)]
    resolved, unmapped = bt.resolve_link_events(evs)
    assert resolved == [(t, "gate_down", "mac-mini"), (t, "gate_trial", "mac-mini"), (t, "gate_up", "mac-mini")]
    assert unmapped == 0
    # a fail-fast API line quotes the gate's reason but is not a gate line
    assert bt.link_event(fail_fast(t, HUB_SYNC), t) is None


def test_marked_down_reason_with_nested_parentheses():
    t = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
    line = gate(t, "marked down (Timed out connecting to Hermes backend after 20000ms (down-gate trial deadline)); "
                   "failing fast until it answers", "hermes-hub")
    assert bt.link_event(line, t) == (
        t, "gate_down", "hermes-hub", "Timed out connecting to Hermes backend after 20000ms (down-gate trial deadline)")


def test_fail_fast_lookback_counts_the_quoted_age(tmp_path):
    """`after 20000ms (down-gate trial deadline), 27s ago`: the request began 47 s before the line."""
    t = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
    install(tmp_path, fail_fast(t, HUB_SYNC, ago=27) + "\n" + timeout(t, BOARD) + "\n")
    got = [(route, look) for _, route, _, look in bt.iter_timeouts([tmp_path / "logs" / "desktop.log"])]
    assert got == [(HUB_SYNC, 47.0), (BOARD, 4.0)]

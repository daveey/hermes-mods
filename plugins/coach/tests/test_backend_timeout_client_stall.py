"""backend_timeouts: several remote links stalling together is the Desktop host's uplink, not one link's saturation.

2026-11-13 09:28-09:46 PST (17:28-17:46Z) the detector filed "Desktop link saturation on conn=hermes-hub" and asked
for per-path byte attribution on the hub. No path flooded the link: the hub link carried 31 KB/s, Send-Q was 0, and
`[slow-link]` fired on conn=hermes-hub (direct) and conn=mac-mini (DERP relay) within seconds of each other. The only
shared segment was the MacBook's own uplink. About 120 hub timeouts were a reconnect cascade: in-flight requests
expiring after `[slow-link] ... treating it as down` tore down the master, then the new tunnel's backlog.

The fixture is that real desktop.log slice (09:25-09:50 PST; timeout, `[slow-link]`, `[ssh]` and
`dispatch probe slow` lines; hostnames, the tailnet IP and the user name scrubbed).
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "desktop_uplink_stall_2026-11-13.txt"
spec = importlib.util.spec_from_file_location("backend_timeouts_client_stall_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

_owners = importlib.util.spec_from_file_location("backend_timeouts_owners_helpers_stall",
                                                 Path(__file__).with_name("test_backend_timeout_path_owners.py"))
helpers = importlib.util.module_from_spec(_owners)
_owners.loader.exec_module(helpers)
Board = helpers.Board

END = datetime(2026, 11, 13, 17, 50, tzinfo=timezone.utc)  # 09:50 PST
PST = timezone(timedelta(hours=-8))
BOARD = "/api/plugins/kanban/board"
BOARDS = "/api/plugins/kanban/boards"
HUB_SYNC = "/api/plugins/hub-sync/status"
LABELS = "/api/plugins/kanban/session-labels"
INBOX = "/api/plugins/inbox/items"


@pytest.fixture(autouse=True)
def isolate_power(monkeypatch):
    monkeypatch.setattr(bt, "probe_power", lambda now: {"status": "ok", "intervals": []}, raising=False)


def install(home, text):
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "desktop.log").write_text(text)


def run(home, board=None, now=END, window=25, dry=False):
    argv = ["--home", str(home), "--host", "laptop", "--json", "--window", str(window)]
    if dry:
        argv.append("--dry-run")
    return bt.main(argv, runner=board, now=now, probe=lambda: {"loadavg": [1.0, 1.0, 1.0], "backend_ps": []})


def report(home, capsys, **kw):
    assert run(home, **kw) == 0
    return json.loads(capsys.readouterr().out)


def keys(board):
    return [c[0][c[0].index("--idempotency-key") + 1] for c in board.creates()]


def stamp(t):
    return t.astimezone(PST).strftime("%Y-%m-%d %H:%M:%S,000 -08:00")


def timeout(t, path, conn="hermes-hub"):
    return (f"{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} "
            "Error: Timed out connecting to Hermes backend after 4000ms")


# --- the real incident -------------------------------------------------------------------------------------------

def test_real_uplink_stall_is_client_stall_not_hub_saturation(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text())
    out = report(tmp_path, capsys, dry=True)
    assert out["awake_by_conn"]["hermes-hub"] == 283 and out["awake_by_conn"]["mac-mini"] == 43
    # current main called this "link saturation on conn=hermes-hub"
    assert out["saturated_conns"] == []
    cs = out["client_stall"]
    assert cs["conns"] == ["hermes-hub", "mac-mini"]
    assert cs["unmapped_slow_links"] == 0
    # 09:30 (hub 09:30:31 + mini 09:30:47), 09:40 (09:40:14 + 09:40:41), 09:45 (09:43:01 + 09:43:13 PST) buckets
    assert cs["buckets"] == ["2026-11-13 17:30:00+00:00", "2026-11-13 17:40:00+00:00", "2026-11-13 17:45:00+00:00"]
    assert cs["count"] * 2 > out["awake_count"]
    rc = out["reconnect_cascade"]
    # ~120 hub timeouts: 71 in-flight expiring at 09:41:58 after the 09:41:43 teardown, then the new tunnel's backlog
    assert 100 <= rc["count"] <= 130 and set(rc["by_conn"]) == {"hermes-hub"}, rc
    assert out["uplink_stall"] is True and out["over_threshold"] is True


def test_real_uplink_stall_files_one_uplink_card_per_incident(tmp_path, capsys):
    install(tmp_path, FIXTURE.read_text())
    board = Board()
    out = report(tmp_path, capsys, board=board)
    assert keys(board) == ["backend-timeouts:laptop:uplink-stall"], keys(board)  # no conn card, no path cards
    (argv, body), = board.creates()
    assert argv[-1] == "Desktop uplink stall on laptop"
    assert "tailscale status" in body and "tailscale netcheck" in body and "DERP" in body
    assert "tcpdump" not in body and "Link/connection saturation suspected" not in body
    assert out["cards"][0]["kind"] == "uplink_stall" and out["cards"][0]["action"] == "created"

    # the next pass within the hour reuses it silently; after an hour it comments on the same card
    report(tmp_path, capsys, board=board, now=END + timedelta(minutes=5), window=30)
    assert len(board.creates()) == 1 and board.comments() == []
    report(tmp_path, capsys, board=board, now=END + timedelta(minutes=5, hours=1), window=30 + 60 + 5)
    assert len(board.creates()) == 1
    assert [c[0][3] for c in board.comments()] == [board.keys["backend-timeouts:laptop:uplink-stall"]]

    # once it closes, a recurrence files :2
    board.tasks[board.keys["backend-timeouts:laptop:uplink-stall"]]["task"]["status"] = "done"
    report(tmp_path, capsys, board=board, now=END + timedelta(hours=2, minutes=10), window=30 + 120 + 10)
    assert keys(board)[-1] == "backend-timeouts:laptop:uplink-stall:2"


# --- a single saturated conn stays saturation ----------------------------------------------------------------------

def board_storm_lines(now):
    """Modeled on the 2026-11-13 13:59-14:29Z kanban/board storm: only the hub tunnel bursts across paths, the hub
    also logs one `[slow-link] ... is slow` for its own port, and mac-mini times out a couple of times far apart
    with no slow-link of its own."""
    lines = [f"{stamp(now - timedelta(minutes=29))} [hermes] [ssh] forwarding 127.0.0.1:50001 -> 127.0.0.1:9147",
             f"{stamp(now - timedelta(minutes=29))} [hermes] [ssh] forwarding 127.0.0.1:50002 -> 127.0.0.1:9147"]
    for burst_start in (now - timedelta(minutes=27), now - timedelta(minutes=18), now - timedelta(minutes=9)):
        if burst_start == now - timedelta(minutes=18):
            lines.append(f"{stamp(burst_start)} [hermes] [slow-link] http://127.0.0.1:50001 is slow (probe timed out "
                         "with the SSH connection alive); keeping the connection, stretching timeouts x4")
            lines.append(f"{stamp(burst_start)} [hermes] [remote-timing] conn:hermes-hub::default dispatch probe "
                         "slow (10010 ms)")
        for s in range(12):
            t = burst_start + timedelta(seconds=s)
            lines += [timeout(t, BOARD)] * 3 + [timeout(t, BOARDS), timeout(t, HUB_SYNC)]
            if s % 3 == 0:
                lines.append(timeout(t, LABELS))
    lines += [timeout(now - timedelta(minutes=22), INBOX, "mac-mini"),
              timeout(now - timedelta(minutes=4), INBOX, "mac-mini")]
    return lines


def test_single_conn_burst_without_another_remote_stall_is_still_saturation(tmp_path, capsys):
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    install(tmp_path, "\n".join(board_storm_lines(now)) + "\n")
    out = report(tmp_path, capsys, now=now, window=30, dry=True)
    assert out["saturated_conns"] == ["hermes-hub"]
    assert out["client_stall"]["count"] == 0 and out["client_stall"]["conns"] == []
    assert out["reconnect_cascade"]["count"] == 0
    assert out["uplink_stall"] is False

    board = Board()
    report(tmp_path, capsys, board=board, now=now, window=30)
    assert "Desktop uplink stall on laptop" not in [c[0][-1] for c in board.creates()]
    assert "Link/connection saturation suspected on conn=hermes-hub" in board.creates()[0][1]


def test_local_conn_stalling_with_one_remote_is_not_a_client_stall(tmp_path, capsys):
    """`local`/`primary` never count toward the two remote conns."""
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    lines = board_storm_lines(now)
    t = now - timedelta(minutes=18)
    lines += [timeout(t + timedelta(seconds=s), p, "local") for s in range(3) for p in (BOARD, INBOX)]
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, now=now, window=30, dry=True)
    assert out["client_stall"]["count"] == 0 and out["saturated_conns"] == ["hermes-hub"]


# --- port mapping and cascades -------------------------------------------------------------------------------------

def test_slow_link_port_maps_by_forwarding_line_then_probe_slow():
    t0 = datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc)
    ev = [
        (t0, "forward", "hermes-hub", "5001"),                       # a forwarding line that names its conn
        (t0 + timedelta(seconds=10), "slow", None, "5001"),
        (t0 + timedelta(seconds=20), "forward", None, "5002"),       # today's Desktop names none ...
        (t0 + timedelta(seconds=30), "probe_slow", "mac-mini", None),
        (t0 + timedelta(seconds=30), "slow", None, "5002"),          # ... so the same-instant probe line maps it
        (t0 + timedelta(seconds=90), "slow", None, "5002"),          # and the mapping holds for that port
        (t0 + timedelta(seconds=95), "forward", None, "5002"),       # until it is forwarded again
        (t0 + timedelta(seconds=99), "slow", None, "5002"),          # unmappable: counted, never guessed
    ]
    out, unmapped = bt.resolve_link_events(ev)
    slow = [(int((t - t0).total_seconds()), c) for t, k, c in out if k == "slow"]
    assert slow == [(10, "hermes-hub"), (30, "mac-mini"), (90, "mac-mini")]
    assert unmapped == 1


def test_teardown_cascade_is_counted_apart_and_kept_out_of_saturation(tmp_path, capsys):
    now = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)
    lines = []
    for down in (now - timedelta(minutes=20), now - timedelta(minutes=10)):
        lines.append(f"{stamp(down)} [hermes] [ssh] teardown conn:hermes-hub::default (revalidate): release")
        for s in range(15, 25):  # in-flight requests expiring together 15-25 s after the teardown
            t = down + timedelta(seconds=s)
            lines += [timeout(t, BOARD), timeout(t, BOARDS), timeout(t, HUB_SYNC), timeout(t, LABELS)]
    install(tmp_path, "\n".join(lines) + "\n")
    out = report(tmp_path, capsys, now=now, window=30, dry=True)
    assert out["reconnect_cascade"] == {"count": 80, "by_conn": {"hermes-hub": 80}, "teardowns": 2}
    assert out["saturated_conns"] == []
    assert out["awake_conn_bursts"] == {}


def test_cancelled_forward_right_after_teardown_maps_its_port_to_that_conn():
    """M3g: today's Desktop names no conn on `forwarding`/`cancelled forward`; a cancel within CASCADE_S of
    `teardown conn:<c>` is the only thing that maps that port, so a later `[slow-link]` on it belongs to <c>."""
    t0 = datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc)
    timed = [
        (0, "[ssh] forwarding 127.0.0.1:50003 -> 127.0.0.1:9147"),
        (60, "[ssh] teardown conn:hermes-hub::default (revalidate): release"),
        (65, "[ssh] cancelled forward 127.0.0.1:50003"),
        (120, "[slow-link] http://127.0.0.1:50003 is slow (probe timed out with the SSH connection alive); "
              "keeping the connection, stretching timeouts x4"),
        # a cancel long after the teardown maps nothing
        (200, "[ssh] cancelled forward 127.0.0.1:50004"),
        (210, "[slow-link] http://127.0.0.1:50004 is slow (probe timed out with the SSH connection alive); "
              "keeping the connection, stretching timeouts x4"),
    ]
    ev = []
    for s, text in timed:
        ts = t0 + timedelta(seconds=s)
        e = bt.link_event(f"{stamp(ts)} [hermes] {text}", ts)
        assert e is not None, text
        ev.append(e)
    out, unmapped = bt.resolve_link_events(ev)
    slow = [(int((t - t0).total_seconds()), c) for t, k, c in out if k == "slow"]
    assert slow == [(120, "hermes-hub")]
    assert unmapped == 1


# --- a stall-dominant window with a big unexplained rest ----------------------------------------------------------

MIXED_NOW = datetime(2026, 11, 13, 14, 29, tzinfo=timezone.utc)


def mixed_window_lines():
    """14:10:00-14:10:19Z (one 5-minute bucket): hermes-hub and mac-mini both burst across two paths in the same
    seconds (a client stall; hermes-hub's kanban/board twice a second): 100 timeouts. 14:20:00-14:22:50Z: 35 hub-sync
    timeouts on hermes-hub alone, one every 5 s (no burst, no second conn): unexplained, and over the threshold of 30
    by itself."""
    stall = datetime(2026, 11, 13, 14, 10, tzinfo=timezone.utc)
    lines = []
    for s in range(20):
        t = stall + timedelta(seconds=s)
        lines += [timeout(t, BOARD), timeout(t, BOARD), timeout(t, BOARDS),
                  timeout(t, INBOX, "mac-mini"), timeout(t, LABELS, "mac-mini")]
    rest = datetime(2026, 11, 13, 14, 20, tzinfo=timezone.utc)
    lines += [timeout(rest + timedelta(seconds=5 * k), HUB_SYNC) for k in range(35)]
    return lines


def run_t(home, board=None, now=MIXED_NOW, window=30, dry=False, threshold=30):
    argv = ["--home", str(home), "--host", "laptop", "--json", "--window", str(window),
            "--threshold", str(threshold)]
    if dry:
        argv.append("--dry-run")
    return bt.main(argv, runner=board, now=now, probe=lambda: {"loadavg": [1.0, 1.0, 1.0], "backend_ps": []})


def test_stall_dominant_window_with_unexplained_rest_over_threshold_files_uplink_and_generic_card(tmp_path, capsys):
    """M3j (README: the generic incident card is filed only when the unexplained rest is over the threshold by
    itself): 100 of 135 awake timeouts are a client stall, so the uplink card is filed; the other 35 are >= 30 on
    their own, so the generic incident card is filed beside it."""
    install(tmp_path, "\n".join(mixed_window_lines()) + "\n")
    assert run_t(tmp_path, dry=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["awake_count"] == 135 and out["client_stall"]["count"] == 100
    assert out["client_stall"]["conns"] == ["hermes-hub", "mac-mini"]
    assert bt.unexplained_count(out) == 35 and out["uplink_stall"] is True

    board = Board()
    assert run_t(tmp_path, board=board) == 0
    out = json.loads(capsys.readouterr().out)
    assert keys(board) == ["backend-timeouts:laptop:uplink-stall", bt.card_key("laptop", MIXED_NOW)], keys(board)
    assert [c.get("kind") for c in out["cards"]] == ["uplink_stall", None]
    assert out["cards"][1]["action"] == "created"


def test_paths_whose_timeouts_are_all_client_stall_get_no_path_card(tmp_path, capsys):
    """M3k: significant_paths sets client_stall/reconnect_cascade timeouts aside. kanban/board's 40 timeouts all fall
    in the stall bucket (40 >= 20% of 135, so it would be significant on raw numbers); only hub-sync, whose 35 stand
    apart, gets a path card while the open generic card waits (todo, no parent naming a path)."""
    install(tmp_path, "\n".join(mixed_window_lines()) + "\n")
    assert run_t(tmp_path, dry=True) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["awake_by_path"][BOARD] == 40 and rep["awake_explained_by_path"][BOARD] == 40
    assert bt.significant_paths(rep, 30) == [HUB_SYNC]

    board = Board()
    board.add("t_host", "todo", "Desktop backend timeouts: 135 in 30 min", "## Top request paths\n")
    bt.save_state(tmp_path, {bt.card_key("laptop", MIXED_NOW): {
        "task_id": "t_host", "sequence": 1, "created": MIXED_NOW.timestamp() - 600,
        "last_comment": MIXED_NOW.timestamp() - 600}})
    assert run_t(tmp_path, board=board) == 0
    out = json.loads(capsys.readouterr().out)
    host_card = out["cards"][-1]
    assert host_card["task_id"] == "t_host" and host_card["action"] == "throttled"  # the generic card is filed
    assert keys(board) == ["backend-timeouts:laptop:uplink-stall", f"backend-timeouts:laptop:{HUB_SYNC}"], keys(board)
    assert set(host_card["path_owners"]) == {HUB_SYNC}


def test_dispatch_probe_slow_on_two_remote_conns_alone_is_a_client_stall(tmp_path, capsys):
    """M3o (README lists `[remote-timing] ... dispatch probe slow` as a stall signal): no `[slow-link]` line and no
    shared burst (one path per conn, never two in the same seconds), only each conn's `dispatch probe slow` 20 s
    apart. That bucket's timeouts are a client stall."""
    t = datetime(2026, 11, 13, 14, 10, tzinfo=timezone.utc)
    lines = [f"{stamp(t)} [hermes] [remote-timing] conn:hermes-hub::default dispatch probe slow (10010 ms)",
             f"{stamp(t + timedelta(seconds=20))} [hermes] [remote-timing] conn:mac-mini::default dispatch probe "
             "slow (9800 ms)"]
    lines += [timeout(t + timedelta(seconds=3 * k), BOARD) for k in range(20)]
    lines += [timeout(t + timedelta(seconds=3 * k + 1), INBOX, "mac-mini") for k in range(15)]
    install(tmp_path, "\n".join(lines) + "\n")
    assert run_t(tmp_path, dry=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["client_stall"]["unmapped_slow_links"] == 0
    cs = out["client_stall"]
    assert cs["conns"] == ["hermes-hub", "mac-mini"] and cs["count"] == 35, cs
    assert cs["buckets"] == ["2026-11-13 14:10:00+00:00"]
    assert out["uplink_stall"] is True

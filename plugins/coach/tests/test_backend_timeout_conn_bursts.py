"""backend_timeouts: one saturated connection gets ONE conn card, not a card per victim path.

2026-11-13 13:34Z: the MacBook's hub tunnel (one ssh master) was saturated by kanban/board refetches. Every path on
`conn=hermes-hub` timed out in the same seconds, and the detector filed three per-path cards (kanban/board,
kanban/boards, hub-sync/status) whose workers each re-diagnosed the same link. A conn whose timeouts spread over
>= 3 paths in shared bursts is now link/connection saturation: one `backend-timeouts:<host>:conn:<conn>` card naming
the top paths as candidates, and no per-path card for paths whose timeouts sit inside those bursts. A single slow
path, whose timeouts do not co-occur with other paths, still gets its own path card.
"""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_timeouts_conn_burst_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

_owners = importlib.util.spec_from_file_location("backend_timeouts_owners_helpers",
                                                 Path(__file__).with_name("test_backend_timeout_path_owners.py"))
helpers = importlib.util.module_from_spec(_owners)
_owners.loader.exec_module(helpers)
Board, seed_catch_all, run, stamp = helpers.Board, helpers.seed_catch_all, helpers.run, helpers.stamp

NOW = helpers.NOW
BOARD = "/api/plugins/kanban/board"
BOARDS = "/api/plugins/kanban/boards"
HUB_SYNC = "/api/plugins/hub-sync/status"
LABELS = "/api/plugins/kanban/session-labels"
INBOX = "/api/plugins/inbox/items"
TIMEOUT = "Error: Timed out connecting to Hermes backend after 4000ms"


@pytest.fixture(autouse=True)
def isolate_power(monkeypatch):
    monkeypatch.setattr(bt, "probe_power", lambda now: {"status": "ok", "intervals": []}, raising=False)


def line(t, path, conn="hermes-hub"):
    return f"{stamp(t)} [hermes] [hermes:api GET {path}] conn={conn} {TIMEOUT}"


def write(home, lines, now=NOW):
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    head = [f"{stamp(now - timedelta(minutes=29))} [hermes] [renderer console:main] start"]
    (logs / "desktop.log").write_text("\n".join(head + lines) + "\n")


def saturated_tunnel_lines(now=NOW):
    """Two bursts (like 05:25 and 05:34): each second of a burst, every path on the hub tunnel times out together."""
    lines = []
    for burst_start in (now - timedelta(minutes=20), now - timedelta(minutes=11)):
        for s in range(20):
            t = burst_start + timedelta(seconds=s)
            lines += [line(t, BOARD)] * 4 + [line(t, BOARDS), line(t, HUB_SYNC)]
            if s % 4 == 0:
                lines.append(line(t, LABELS))
    return lines


def single_slow_path_lines(now=NOW):
    """hub-sync/status alone times out every 10 s; another path times out a few times, never in the same seconds."""
    lines = [line(now - timedelta(minutes=25) + timedelta(seconds=10 * i), HUB_SYNC) for i in range(120)]
    lines += [line(now - timedelta(minutes=24, seconds=5) + timedelta(minutes=3 * i), INBOX) for i in range(5)]
    return lines


def keys(board):
    return [c[0][c[0].index("--idempotency-key") + 1] for c in board.creates()]


def test_burst_across_paths_on_one_conn_files_one_conn_card_and_no_path_cards(tmp_path, capsys):
    write(tmp_path, saturated_tunnel_lines())
    board = Board()
    seed_catch_all(tmp_path, board)  # an open host card waiting behind an unrelated parent: it owns nothing here
    assert run(tmp_path, board) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["saturated_conns"] == ["hermes-hub"]
    assert keys(board) == ["backend-timeouts:macbook-1:conn:hermes-hub"], keys(board)
    (argv, body), = board.creates()
    title = argv[-1]
    assert "conn=hermes-hub" in title and "saturation" in title
    # names the top paths as candidates and asks for per-path byte/request attribution
    cand = body.split("## Candidate paths", 1)[1].split("## Evidence", 1)[0]
    for p in (BOARD, BOARDS, HUB_SYNC):
        assert f"`{p}`" in cand
    assert "tcpdump" in body and "bytes" in body and "t_catch" in body
    owners = out["cards"][0]["path_owners"]
    assert set(owners) == {"conn=hermes-hub"} and owners["conn=hermes-hub"]["action"] == "created"

    # the next pass reuses the conn card (throttled), and still files no path card
    run(tmp_path, board, now=NOW + timedelta(minutes=1))
    capsys.readouterr()
    assert len(board.creates()) == 1


def test_single_slow_path_files_one_path_card_and_no_conn_card(tmp_path, capsys):
    write(tmp_path, single_slow_path_lines())
    board = Board()
    seed_catch_all(tmp_path, board)
    run(tmp_path, board)
    out = json.loads(capsys.readouterr().out)
    assert out["saturated_conns"] == []
    assert keys(board) == [f"backend-timeouts:macbook-1:{HUB_SYNC}"], keys(board)
    assert "saturation" not in board.creates()[0][1]


def test_path_that_stands_apart_from_its_saturated_conn_keeps_its_own_card(tmp_path, capsys):
    """The tunnel is saturated in two bursts, and outside them inbox/items keeps timing out on its own."""
    lines = saturated_tunnel_lines()
    lines += [line(NOW - timedelta(minutes=28) + timedelta(seconds=7 * i), INBOX) for i in range(100)
              if not (8 * 60 - 7 < 7 * i < 8 * 60 + 21 or 17 * 60 - 7 < 7 * i < 17 * 60 + 21)]
    write(tmp_path, lines)
    board = Board()
    seed_catch_all(tmp_path, board)
    run(tmp_path, board)
    out = json.loads(capsys.readouterr().out)
    assert out["saturated_conns"] == ["hermes-hub"]
    assert sorted(keys(board)) == sorted(["backend-timeouts:macbook-1:conn:hermes-hub",
                                          f"backend-timeouts:macbook-1:{INBOX}"]), keys(board)


def test_two_saturated_conns_get_one_card_each(tmp_path, capsys):
    lines = saturated_tunnel_lines()
    for s in range(10):
        t = NOW - timedelta(minutes=15) + timedelta(seconds=s)
        lines += [line(t, BOARD, "mac-mini")] * 3 + [line(t, BOARDS, "mac-mini"), line(t, HUB_SYNC, "mac-mini")]
        t = NOW - timedelta(minutes=5) + timedelta(seconds=s)
        lines += [line(t, BOARD, "mac-mini")] * 3 + [line(t, BOARDS, "mac-mini"), line(t, HUB_SYNC, "mac-mini")]
    write(tmp_path, lines)
    board = Board()
    seed_catch_all(tmp_path, board)
    run(tmp_path, board)
    out = json.loads(capsys.readouterr().out)
    assert out["saturated_conns"] == ["hermes-hub", "mac-mini"]
    assert keys(board) == ["backend-timeouts:macbook-1:conn:hermes-hub", "backend-timeouts:macbook-1:conn:mac-mini"]


def test_active_host_card_naming_the_saturated_conn_owns_it(tmp_path, capsys):
    write(tmp_path, saturated_tunnel_lines())
    board = Board()
    seed_catch_all(tmp_path, board, status="running")
    board.tasks["t_catch"]["task"]["body"] = "Link/connection saturation suspected on conn=hermes-hub: ..."
    run(tmp_path, board)
    out = json.loads(capsys.readouterr().out)
    assert board.creates() == []
    assert out["cards"][0]["path_owners"] == {"conn=hermes-hub": {"owner": "t_catch", "action": "owned"}}


def test_fresh_host_card_reports_the_saturation_and_the_attribution_step(tmp_path, capsys):
    write(tmp_path, saturated_tunnel_lines())
    board = Board()
    run(tmp_path, board)
    capsys.readouterr()
    (argv, body), = board.creates()
    assert keys(board) == [bt.card_key("macbook-1", NOW)]
    assert "Link/connection saturation suspected on conn=hermes-hub" in body
    assert "3. The evidence names link/connection saturation on conn=hermes-hub." in body


def test_spread_timeouts_without_shared_seconds_are_not_saturation():
    """Many paths on one conn, but never in the same bucket: per-path behaviour is unchanged."""
    b = bt.conn_bursts({"hermes-hub": {i: bt.Counter({p: 1}) for i, p in
                                       enumerate([BOARD, BOARDS, HUB_SYNC, LABELS] * 20)}})
    rep = {"awake_count": 80, "awake_conn_bursts": b, "awake_by_path": dict.fromkeys([BOARD, BOARDS, HUB_SYNC], 20)}
    assert bt.saturated_conns(rep, 30) == []
    assert bt.significant_paths(rep, 30) == [BOARD, BOARDS, HUB_SYNC]

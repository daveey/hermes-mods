"""backend_timeouts: every dominant request path needs an owner card, not just the host (self-heal agent-23f6a4d787).

2026-11-13: the MacBook's open incident card sat in todo behind a parent that named only runners/v1/fleet, so the
detector kept commenting there while /api/plugins/hub-sync/status (112 of 193 hub timeouts) had no card at all and
an agent called it "already owned". A path with >= 20% of the awake count (or >= the threshold) now needs an open
card that names it: an active card, or a waiting (todo/scheduled) card behind an open parent that names it. Without
one, the detector files `backend-timeouts:<host>:<path>`.
"""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_timeouts_owners_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

NOW = datetime(2026, 11, 13, 12, 0, 0)
HUB_SYNC = "/api/plugins/hub-sync/status"
FLEET = "/api/plugins/runners/v1/fleet"
BOARD = "/api/plugins/kanban/board"


@pytest.fixture(autouse=True)
def isolate_power(monkeypatch):
    monkeypatch.setattr(bt, "probe_power", lambda now: {"status": "ok", "intervals": []}, raising=False)


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S,000")


def write_log(home, counts, now=NOW):
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lines, i = [f"{stamp(now - timedelta(minutes=29))} [hermes] [renderer console:main] start"], 0
    for path, n in counts.items():
        for _ in range(n):
            t = now - timedelta(minutes=25) + timedelta(seconds=i * 5)
            lines.append(f"{stamp(t)} [hermes] [hermes:api GET {path}] conn=hermes-hub "
                         "Error: Timed out connecting to Hermes backend after 4000ms")
            i += 1
    (logs / "desktop.log").write_text("\n".join(lines) + "\n")


class Board:
    """Stub `hermes kanban create|show|comment` with real-shaped show JSON (task + parent ids)."""

    def __init__(self):
        self.calls, self.keys = [], {}
        self.tasks = {}

    def add(self, tid, status, title, body="", parents=()):
        self.tasks[tid] = {"task": {"id": tid, "status": status, "title": title, "body": body},
                           "parents": list(parents), "children": []}

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        if argv[:3] == ["hermes", "kanban", "create"]:
            key = argv[argv.index("--idempotency-key") + 1]
            new = key not in self.keys
            tid = self.keys.setdefault(key, f"t_new{len(self.keys):04d}")
            if new:
                self.add(tid, "ready", argv[-1], input or "")
            d = {"id": tid} if new else {"id": tid, "created_at": NOW.timestamp() - 3600}
            return SimpleNamespace(returncode=0, stdout=json.dumps(d), stderr="")
        if argv[:3] == ["hermes", "kanban", "show"]:
            d = self.tasks.get(argv[3])
            if d is None:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"no such task: {argv[3]}")
            return SimpleNamespace(returncode=0, stdout=json.dumps(d), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [c for c in self.calls if c[0][:3] == ["hermes", "kanban", "create"]]

    def comments(self):
        return [c for c in self.calls if c[0][:3] == ["hermes", "kanban", "comment"]]


def run(home, board, now=NOW):
    return bt.main(["--home", str(home), "--host", "macbook-1", "--json"], runner=board, now=now,
                   probe=lambda: {"loadavg": [3.0, 3.0, 3.0], "backend_ps": []})


def seed_catch_all(home, board, status="todo"):
    """Yesterday's open host card: a catch-all waiting in todo behind a parent that names only fleet."""
    board.add("t_fleet", "running", "Desktop→hub runners/v1/fleet 4 s timeouts",
              "Fix the fleet endpoint latency on the hub.")
    board.add("t_catch", status, "Desktop backend timeouts: 193 in 30 min",
              "## Top request paths\n- `/api/sessions/<id>`: 3\n", parents=["t_fleet"])
    yday = bt.card_key("macbook-1", NOW - timedelta(days=1))
    st = {yday: {"task_id": "t_catch", "sequence": 1, "created": NOW.timestamp() - 86400, "last_comment": 0}}
    bt.save_state(home, st)


def test_unowned_dominant_path_gets_its_own_card_while_owned_path_only_comments(tmp_path, capsys):
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51, BOARD: 26})
    board = Board()
    seed_catch_all(tmp_path, board)
    assert run(tmp_path, board) == 0
    out = json.loads(capsys.readouterr().out)
    keys = [c[0][c[0].index("--idempotency-key") + 1] for c in board.creates()]
    assert keys == [f"backend-timeouts:macbook-1:{HUB_SYNC}"], keys  # hub-sync has no owner: one new card
    (argv, body), = board.creates()
    assert "hub-sync/status" in argv[-1] and "macbook-1" not in argv[-1]
    assert "t_catch" in body and "112" in body
    # fleet is owned (its parent names it) and kanban/board is under 20% and the threshold: no card for either
    assert [c[0][3] for c in board.comments()] == ["t_catch"]
    owners = out["cards"][0]["path_owners"]
    assert owners[HUB_SYNC]["action"] == "created" and owners[FLEET] == {"owner": "t_fleet", "action": "owned"}
    assert BOARD not in owners


def test_path_card_is_reused_and_comments_hourly_then_recurs_when_closed(tmp_path, capsys):
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51})
    board = Board()
    seed_catch_all(tmp_path, board)
    run(tmp_path, board)
    tid = board.keys[f"backend-timeouts:macbook-1:{HUB_SYNC}"]
    run(tmp_path, board, now=NOW + timedelta(minutes=10))  # throttled: no new card, no comment on it
    assert len(board.creates()) == 1 and tid not in [c[0][3] for c in board.comments()]
    later = NOW + timedelta(minutes=61)
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51}, now=later)
    run(tmp_path, board, now=later)
    assert len(board.creates()) == 1 and tid in [c[0][3] for c in board.comments()]
    board.tasks[tid]["task"]["status"] = "done"
    run(tmp_path, board, now=later + timedelta(minutes=10))
    keys = [c[0][c[0].index("--idempotency-key") + 1] for c in board.creates()]
    assert keys[-1] == f"backend-timeouts:macbook-1:{HUB_SYNC}:2"
    assert tid in board.creates()[-1][1]  # the recurrence names the closed card
    capsys.readouterr()


def test_active_host_card_naming_every_path_files_nothing_new(tmp_path, capsys):
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51})
    board = Board()
    seed_catch_all(tmp_path, board, status="running")
    board.tasks["t_catch"]["task"]["body"] = f"- `{HUB_SYNC}`: 100\n- `{FLEET}`: 40\n"
    run(tmp_path, board)
    capsys.readouterr()
    assert board.creates() == [] and [c[0][3] for c in board.comments()] == ["t_catch"]


def test_fresh_host_card_owns_the_paths_it_lists(tmp_path, capsys):
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51})
    board = Board()
    run(tmp_path, board)
    out = json.loads(capsys.readouterr().out)
    assert len(board.creates()) == 1 and out["cards"][0]["action"] == "created"
    run(tmp_path, board, now=NOW + timedelta(minutes=10))  # the card it filed names both paths
    capsys.readouterr()
    assert len(board.creates()) == 1


def test_scoped_path_acceptance(tmp_path, capsys):
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51})
    board = Board()
    seed_catch_all(tmp_path, board)
    run(tmp_path, board)
    body = board.creates()[0][1]
    assert f"no `{HUB_SYNC}` entry in" in body
    assert "`awake_by_path`" in body
    assert "prints `\"count\": 0`" not in body
    assert "If incident `t_catch` completes with aggregate count 0" in body
    assert "Do not link this card behind another path card." in body
    capsys.readouterr()


def test_scoped_conn_acceptance(tmp_path, monkeypatch):
    board = Board()
    seed_catch_all(tmp_path, board)
    monkeypatch.setattr(bt, "saturated_conns", lambda *a: ["peer"])
    rep = {"awake_count": 60, "count": 60, "by_path": {}, "awake_by_path": {},
           "awake_by_conn": {"peer": 60}, "awake_conn_bursts": {"peer": {
               "count": 60, "in_bursts": 60, "burst_buckets": 4,
               "paths": {HUB_SYNC: 60}, "burst_by_path": {HUB_SYNC: 60}}}}
    monkeypatch.setattr(bt, "_evidence", lambda *a: "evidence")
    bt.file_path_cards(tmp_path, "macbook-1", NOW, rep, {}, 50, 30, board,
                       {"action": "throttled", "task_id": "t_catch"})
    body = board.creates()[0][1]
    assert "no `peer` entry in" in body and "`awake_by_conn`" in body
    assert "prints `\"count\": 0`" not in body
    assert "If incident `t_catch` completes with aggregate count 0" in body
    assert "Do not link this card behind another path card." in body


def seed_closed_incident(home, board):
    seed_catch_all(home, board, status="done")
    board.add("t_path", "scheduled", "path fix")
    st = bt.load_state(home)
    st[bt.PATHS_KEY] = {f"macbook-1:{HUB_SYNC}": {
        "task_id": "t_path", "created": NOW.timestamp() - 7200, "last_comment": 0,
        "host_task_id": "t_catch", "path": HUB_SYNC, "host": "macbook-1"}}
    bt.save_state(home, st)


def test_closed_incident_clear_path_comment_throttled(tmp_path, capsys):
    board = Board()
    seed_closed_incident(tmp_path, board)
    write_log(tmp_path, {FLEET: 2})  # below threshold must still notify
    run(tmp_path, board)
    report = json.loads(capsys.readouterr().out)
    assert len(board.comments()) == 1
    argv = board.comments()[0][0]
    assert argv[3] == "t_path"
    assert report["window_start"] in argv[-1] and report["window_end"] in argv[-1]
    assert '"count": 2' in argv[-1]
    run(tmp_path, board, now=NOW + timedelta(minutes=1))
    assert len(board.comments()) == 1
    assert not board.creates()
    capsys.readouterr()


def test_closed_incident_present_path_no_comment(tmp_path, capsys):
    board = Board()
    seed_closed_incident(tmp_path, board)
    write_log(tmp_path, {HUB_SYNC: 2})
    run(tmp_path, board)
    assert board.comments() == []
    capsys.readouterr()


@pytest.mark.parametrize("incident_status", ["running", "archived", "unreadable"])
def test_recovery_requires_done_incident(tmp_path, capsys, incident_status):
    board = Board()
    seed_closed_incident(tmp_path, board)
    if incident_status == "unreadable":
        del board.tasks["t_catch"]
    else:
        board.tasks["t_catch"]["task"]["status"] = incident_status
    run(tmp_path, board)
    assert board.comments() == []
    capsys.readouterr()


@pytest.mark.parametrize("path_status", ["done", "archived", "unreadable"])
def test_recovery_requires_open_path(tmp_path, capsys, path_status):
    board = Board()
    seed_closed_incident(tmp_path, board)
    if path_status == "unreadable":
        del board.tasks["t_path"]
    else:
        board.tasks["t_path"]["task"]["status"] = path_status
    run(tmp_path, board)
    assert board.comments() == []
    capsys.readouterr()


def test_recovery_legacy_body_and_failed_comment_throttle(tmp_path, capsys):
    board = Board()
    seed_closed_incident(tmp_path, board)
    st = bt.load_state(tmp_path)
    ent = st[bt.PATHS_KEY][f"macbook-1:{HUB_SYNC}"]
    del ent["host_task_id"]
    del ent["path"]
    bt.save_state(tmp_path, st)
    board.tasks["t_path"]["task"]["body"] = "the incident card `t_catch` is not working on it"

    def fail_comments(argv, **kwargs):
        res = board(argv, **kwargs)
        if argv[:3] == ["hermes", "kanban", "comment"]:
            res.returncode = 1
        return res

    run(tmp_path, fail_comments)
    run(tmp_path, fail_comments, NOW + timedelta(minutes=1))
    assert len(board.comments()) == 1
    run(tmp_path, fail_comments, NOW + timedelta(seconds=bt.COMMENT_EVERY_S))
    assert len(board.comments()) == 2
    capsys.readouterr()


def test_host_body_golden(monkeypatch):
    monkeypatch.setattr(bt, "_evidence", lambda *a: "EVIDENCE")
    monkeypatch.setattr(bt, "home_pin_note", lambda *a: "PIN")
    monkeypatch.setattr(bt, "_saturation_step", lambda *a: "STEP\n")
    assert bt.card_body("host", {}, {}, 50, 30) == '''The Desktop backend timed out on requests. Users see this as chats that won't load,
archive asking "task status unavailable", stale boards.

PIN

## Evidence
EVIDENCE

## Do this
1. Find what makes the backend slow (it is usually CPU-starved or a request is doing heavy work per call): time the
   top paths above against the local backend, `ps`/`sample` the busiest `hermes serve`, and check recent core
   commits for per-request work (e.g. 2026-10-27: a per-request kanban integrity_check, fixed in f2d0e23785).
2. Fix the cause (not just a restart). Use the five-whys skill for the chain.
STEP

## Acceptance
- After the fix, a fresh 30-minute window shows zero timeouts:
  `python3 ~/code/hermes-mods/plugins/coach/backend_timeouts.py --dry-run` prints `"count": 0` with `window_start`
  later than the fix. A restart alone, or a count taken right after a restart, does not satisfy this.
- Paste that output in a comment before completing.
'''


def test_dry_run_files_no_path_cards(tmp_path, capsys):
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51})
    board = Board()
    seed_catch_all(tmp_path, board)
    bt.main(["--home", str(tmp_path), "--host", "macbook-1", "--dry-run"], runner=board, now=NOW,
            probe=lambda: {})
    capsys.readouterr()
    assert board.calls == []

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


def test_dry_run_files_no_path_cards(tmp_path, capsys):
    write_log(tmp_path, {HUB_SYNC: 112, FLEET: 51})
    board = Board()
    seed_catch_all(tmp_path, board)
    bt.main(["--home", str(tmp_path), "--host", "macbook-1", "--dry-run"], runner=board, now=NOW,
            probe=lambda: {})
    capsys.readouterr()
    assert board.calls == []

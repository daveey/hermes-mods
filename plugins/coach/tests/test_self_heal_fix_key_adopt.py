"""A fix card already filed under the lane's fix key on another gateway is adopted, never duplicated (t_01a1d61d26b87b75).

Incident (agent-6ba9ac9880, 2026-11-13): VERIFY t_01a1d5f199d422fe hand-filed fix t_01a1d5f7e4ceefd1 under
`selfheal:agent-6ba9ac9880:fix` on worker-8's board at 17:32Z. The hub lane's `file_fix` then filed
t_01a1d5fbf3f777f2 with the same key at 17:37Z: `hermes kanban create --idempotency-key` only sees the board it
creates on, so two workers built the same core fix (f0b558ee35, d148b94199). The hub replica carried the first card
with its idempotency_key the whole time.
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_fix_key_adopt_test", ROOT / "self_heal.py")
core = load("coach_core_fix_key_adopt_test", ROOT / "core.py")

NOW = 1794591421.0
FP = "agent-6ba9ac9880"
KEY = "selfheal:agent-6ba9ac9880:fix"
REMOTE = "t_01a1d5f7e4ceefd1"


def OPEN(board=None, now=None):
    return {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True, "ok_running": True, "available": True, "why": ""}


class Board:
    """Fake `hermes kanban`: create returns a new id (the hub board never held the remote card)."""

    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if "show" in argv:
            tid = argv[argv.index("show") + 1]
            return SimpleNamespace(returncode=1, stdout="", stderr=f"no such task: {tid}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def hub_db(path, tasks):
    """A hermes-hub replica: tasks = [(gw, board, id, status, idempotency_key)], owner-qualified rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("create table replica (gw text, coll text, key text, version integer, seq integer, data text, "
               "updated_at integer, primary key (gw, coll, key))")
    for gw, board, tid, status, key in tasks:
        db.execute("insert into replica values(?,?,?,?,?,?,?)",
                   (gw, "tasks", f"default/{board}/{tid}", 1, 1, json.dumps(
                       {"id": tid, "status": status, "board": board, "gateway": gw, "profile": "default",
                        "idempotency_key": key, "title": "self-heal fix: incident 0a89a428ad05"}), 1))
    db.commit()
    db.close()
    return path


def board_db(path, rows):
    """A local board: rows = [(id, status, idempotency_key)]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("create table tasks(id text primary key, title text, status text, created_at int, "
               "idempotency_key text)")
    db.executemany("insert into tasks values(?,?,?,?,?)", [(i, "t", s, 1, k) for i, s, k in rows])
    db.commit()
    db.close()


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    board_db(home / "kanban.db", [("t_local0001", "running", None)])
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("COACH_SELFHEAL_NO_EXEC", "1")
    monkeypatch.setattr(sh, "coach_root", lambda: tmp_path / "coach")
    monkeypatch.setattr(sh, "remote_verify", lambda: True)
    s = core.Store(tmp_path / "coach")
    (tmp_path / "coach" / "selfheal" / FP).mkdir(parents=True)
    (tmp_path / "coach" / "selfheal" / FP / "proposal.md").write_text(
        "Receipt timeout latches the worker store\n\n**Plan**\nCore: retry worker_receipt_timeout.\n")
    sh.LaneState(s.root).update(FP, status="proposed", component="agent", card="t_01a1d5a7cd1406c0",
                                symptom="WorkerPersistenceError in _apply (runtime_session_store.py)")
    return s


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def test_fix_card_on_another_gateway_under_the_lane_key_is_adopted_not_duplicated(store, tmp_path, monkeypatch):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("worker-8", "default", REMOTE, "running", KEY),
                                               ("worker-8", "default", "t_other0001", "running", "selfheal:x:fix")])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner=None: (tmp_path / "home", hdb))
    b = Board()
    out = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert b.creates() == [], "a second fix card was filed under a key another gateway already holds"
    assert out["card"] == REMOTE and out["action"] == "adopted"
    st = sh.LaneState(store.root).get(FP)
    assert (st["status"], st["fix_card"], st.get("verify_card")) == ("approved", REMOTE, None)
    assert (st["owner_card"], st["owner_gateway"], st["owner_board"]) == (REMOTE, "worker-8", "default")
    (e,) = ev(store, "selfheal_fix_adopted_existing")
    assert (e["card"], e["key"], e["gateway"], e["authority"]) == (REMOTE, KEY, "worker-8", "replica")
    # A second Do-it pass is a plain skip: no verify is filed against a remote owner that may still be running
    # (reconcile files it from the completed owner's receipt).
    again = sh.file_fix(FP, store=store, runner=b, now=NOW + 60, budget_fn=OPEN)
    assert again["action"] == "skip" and b.creates() == []


def test_fix_card_on_another_local_board_under_the_lane_key_is_adopted(store, tmp_path, monkeypatch):
    board_db(tmp_path / "home" / "kanban" / "boards" / "hermes" / "kanban.db", [("t_onhermes01", "ready", KEY)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner=None: (tmp_path / "home", None))
    b = Board()
    out = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert b.creates() == [] and out["card"] == "t_onhermes01"
    st = sh.LaneState(store.root).get(FP)
    assert (st["fix_card"], st["owner_board"], st["owner_gateway"]) == ("t_onhermes01", "hermes", None)


@pytest.mark.parametrize("status, key", [("archived", KEY), ("running", "selfheal:agent-6ba9ac9880:r2:fix")])
def test_archived_or_other_generation_cards_do_not_block_the_lane_fix(store, tmp_path, monkeypatch, status, key):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("worker-8", "default", REMOTE, status, key)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner=None: (tmp_path / "home", hdb))
    b = Board()
    out = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert out["action"] == "filed" and len([a for a in b.creates() if "--idempotency-key" in a
                                             and a[a.index("--idempotency-key") + 1] == KEY]) == 1
    assert not ev(store, "selfheal_fix_adopted_existing")


def test_an_earlier_card_of_this_lane_is_not_adopted(store, tmp_path, monkeypatch):
    """A key that hands back a card the lane already knows is an error path, never an approval."""
    sh.LaneState(store.root).update(FP, cards=[REMOTE])
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("worker-8", "default", REMOTE, "running", KEY)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner=None: (tmp_path / "home", hdb))
    sh.file_fix(FP, store=store, runner=Board(), now=NOW, budget_fn=OPEN)
    assert not ev(store, "selfheal_fix_adopted_existing")
    assert sh.LaneState(store.root).get(FP).get("fix_card") != REMOTE


def test_unreadable_replica_files_as_before(store, tmp_path, monkeypatch):
    bad = tmp_path / "hub" / "hub.db"
    bad.parent.mkdir()
    bad.write_bytes(b"not a database")
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner=None: (tmp_path / "home", bad))
    b = Board()
    assert sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)["action"] == "filed"
    assert len(b.creates()) >= 1


def test_verify_briefs_reserve_selfheal_keys_for_the_lane():
    st = {"symptom": "x"}
    for body in (sh.verify_brief(FP, st, REMOTE), sh.verify_brief(FP, st, REMOTE, remote=True)):
        assert "`selfheal:`" in body and "never" in body.lower()

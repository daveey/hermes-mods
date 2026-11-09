"""self-heal lane admission: fixes are not starved by new investigations.

2026-11-09 11:23 on the hub (t_01a1c00ca6bc0945): 40 proposals had waited up to 112 h for fix cards. The lane cron
retried them every pass and each was refused "self-heal concurrency limit reached"; whenever a slot freed, the next
investigation admission (signals sweep, deferred retry, hook) took it first, and a burst of 5 investigations was
admitted in 10 s against a cap of 3 because just-created cards are `ready`, not yet `running`, when the next
admission counts.
"""
import importlib.util
import json
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


sh = load("coach_self_heal_fix_first_test", ROOT / "self_heal.py")
core = load("coach_core_test_fix_first", ROOT / "core.py")

NOW = 1792660000.0
HUB = {}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_SELFHEAL_AUTO_APPROVE", "1")
    (tmp_path / "home").mkdir()
    return core.Store(tmp_path / "coach")


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    rows = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
    return [r for r in rows if r.get("event") == name]


def defect(**kw):
    d = {"component": "desktop", "symptom": "the panel is blank after reload", "source": "hook", "kind": "hermes_bug",
         "p_defect": 0.9, "session_id": "s1", "quote": "the panel is blank", "host": "hub", "platform": "desktop",
         "at": NOW}
    d.update(kw)
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


class Board:
    """Fake engine_budget over a mutable card list; `create` adds a READY card (the dispatcher has not claimed it)."""

    def __init__(self, cards=None, usd=10.0, cap=200.0):
        self.cards, self.usd, self.cap, self.n = list(cards or []), usd, cap, 0

    def eb(self):
        def check(snap, lane, extra=0.0):
            t = snap["lanes"][lane] + extra
            return (False, f"{lane} lane spend today ${t:.2f} >= ${self.cap:g} cap") if t >= self.cap else (True, "")

        return SimpleNamespace(
            snapshot=lambda **kw: {"lanes": {"selfheal": self.usd}, "caps": {"selfheal": self.cap}},
            check=check, engine_cards=lambda home, b, st: [c for c in self.cards if c["status"] in st],
            card_budget=lambda c, snap: 8.0, card_cost=lambda home, b, tid: 8.0)  # fully spent: no $ reservation

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        if "create" in argv:
            self.n += 1
            tid = f"t_{self.n:08x}"
            self.cards.append({"id": tid, "lane": "selfheal", "status": "ready",
                               "title": argv[argv.index("create") + 1]})
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    def creates(self):
        return [c for c in self.cards if c["id"].startswith("t_0")]


def running(title):
    return {"id": f"t_r{abs(hash(title)) % 10**6}", "lane": "selfheal", "status": "running", "title": title}


def propose(store, fp="agent-0123456789", component="agent"):
    sh.LaneState(store.root).update(fp, status="proposed", component=component, symptom="a real defect",
                                    card="t_inv", proposed_at=NOW - 3 * 86400, filed_at=NOW - 4 * 86400)
    return fp


def test_ready_cards_occupy_concurrency_slots(monkeypatch):
    board = Board([running("self-heal: a"), running("self-heal: b"),
                   {"id": "t_c", "lane": "selfheal", "status": "ready", "title": "self-heal: c"}])
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    b = sh.budget(now=NOW)
    assert b["running"] == 2 and b["occupied"] == 3
    assert not b["ok_running"]


def test_burst_of_investigations_cannot_overshoot_the_cap(store, monkeypatch):
    """The 11:23 replay: five admissions in one pass, each new card still `ready` when the next one counts."""
    board = Board()
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    outs = [sh.file_defect(defect(symptom=f"distinct {w} panel defect", session_id=f"s{w}"), store=store, m="live",
                           runner=board, now=NOW, cfg=HUB) for w in ("alpha", "bravo", "charlie", "delta", "echo")]
    assert len(board.creates()) == sh.MAX_RUNNING
    assert [o["action"] for o in outs].count("deferred") == 5 - sh.MAX_RUNNING


def test_investigation_defers_while_proposals_wait_and_an_investigation_runs(store, monkeypatch):
    board = Board([running("self-heal: already investigating")])
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    propose(store)
    out = sh.file_defect(defect(), store=store, m="live", runner=board, now=NOW, cfg=HUB)
    assert out["action"] == "deferred" and "fix" in out["why"]
    assert not board.creates()
    # persisted, not dropped: the deferred-retry pass re-admits it later
    import sqlite3
    db = sqlite3.connect(store.root / "selfheal_pending.sqlite")
    assert db.execute("select count(*) from pending").fetchone()[0] == 1


def test_the_freed_slot_goes_to_the_waiting_fix(store, monkeypatch):
    board = Board([running("self-heal: already investigating")])
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    fp = propose(store)
    sh.file_defect(defect(), store=store, m="live", runner=board, now=NOW, cfg=HUB)
    r = sh.file_fix(fp, store=store, runner=board, now=NOW, trigger="auto")
    assert r["action"] == "filed"
    assert sh.LaneState(store.root).get(fp)["status"] == "approved" and ev(store, "selfheal_approved")


def test_one_investigation_slot_stays_open_while_fixes_wait(store, monkeypatch):
    """Urgent signals still get investigated: with no investigation running, one is admitted."""
    board = Board([running("self-heal fix: something")])
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    propose(store)
    out = sh.file_defect(defect(), store=store, m="live", runner=board, now=NOW, cfg=HUB)
    assert out["action"] == "filed"


def test_investigation_leaves_dollar_headroom_for_a_waiting_fix(store, monkeypatch):
    # 10 + 8 (new investigation) = 18 < 30 admits by itself, but 18 + 15 (fix) = 33 >= 30
    board = Board(usd=10.0, cap=30.0)
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    propose(store)
    out = sh.file_defect(defect(), store=store, m="live", runner=board, now=NOW, cfg=HUB)
    assert out["action"] == "deferred" and "fix" in out["why"] and not board.creates()


def test_no_waiting_fix_keeps_the_old_admission(store, monkeypatch):
    board = Board([running("self-heal: one"), running("self-heal: two")])
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    out = sh.file_defect(defect(), store=store, m="live", runner=board, now=NOW, cfg=HUB)
    assert out["action"] == "filed"


def test_manual_approval_proposals_do_not_hold_investigations(store, monkeypatch):
    """With auto-approve off a proposal waits on David, not on a slot: it must not starve investigations."""
    monkeypatch.setenv("COACH_SELFHEAL_AUTO_APPROVE", "0")
    board = Board([running("self-heal: already investigating")])
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    propose(store)
    out = sh.file_defect(defect(), store=store, m="live", runner=board, now=NOW, cfg=HUB)
    assert out["action"] == "filed"

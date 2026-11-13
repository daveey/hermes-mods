"""Missing lifecycle receipts and admission deferrals survive sync/process restarts."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_self_heal_fix_placement import Board, FP, NOW, OPEN, core, fields, sh, store


def events(store, kind):
    path = store.root / "log.jsonl"
    return [r for r in map(json.loads, path.read_text().splitlines()) if r.get("event") == kind] if path.exists() else []


@pytest.mark.parametrize("adopted", [False, True])
def test_completed_owner_without_receipt_gets_one_verifier(store, monkeypatch, adopted):
    monkeypatch.setattr(sh, "budget", OPEN)
    lane = sh.LaneState(store.root)
    lane.update(FP, status="approved", fix_card="t_abcdef01", card="t_abcdef02" if adopted else "t_abcdef03")
    board = Board(status={"t_abcdef01": "done"})
    sh.reconcile(store, runner=board, now=NOW)
    st = lane.get(FP)
    assert st["status"] == "approved"
    assert st["verify_card"] == "t_00000001"
    verify = fields(board.creates()[0])
    assert verify["parents"] == ["t_abcdef01"]
    assert "not proof" in verify["body"]
    assert "cards wait although a worker is idle" in verify["body"]
    assert "event fixed" in verify["body"]
    sh.reconcile(store, runner=board, now=NOW + 60)
    assert len(board.creates()) == 1
    assert not events(store, "selfheal_fixed")
    # A verifier's explicit receipt, not a done card/commit-looking summary, closes the lane.
    sh.record_event("fixed", FP, card="t_abcdef01", note="abcdef1234", store=store, now=NOW + 90)
    sh.reconcile(store, runner=board, now=NOW + 120)
    assert lane.get(FP)["status"] == "fixed"


def test_failed_receipt_verifier_creation_retries_without_stale(store, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    sh.LaneState(store.root).update(FP, status="approved", fix_card="t_abcdef01")
    board = Board(fail={1}, status={"t_abcdef01": "done"})
    sh.reconcile(store, runner=board, now=NOW)
    assert sh.LaneState(store.root).get(FP)["status"] == "approved"
    sh.reconcile(store, runner=board, now=NOW + 60)
    assert sh.LaneState(store.root).get(FP)["verify_card"] == "t_00000002"


def test_receipt_recovery_honors_cap_and_preserves_board(store, monkeypatch):
    capped = {**OPEN(), "ok_usd": False, "why": "daily cap"}
    monkeypatch.setattr(sh, "budget", lambda *a: capped)
    sh.LaneState(store.root).update(FP, status="approved", fix_card="t_abcdef01", board="ops")
    board = Board(status={"t_abcdef01": "done"})
    sh.reconcile(store, runner=board, now=NOW)
    assert not board.creates()
    assert sh.LaneState(store.root).get(FP)["status"] == "approved"
    monkeypatch.setattr(sh, "budget", OPEN)
    sh.reconcile(store, runner=board, now=NOW + 60)
    argv = board.creates()[0]
    assert argv[argv.index("--board") + 1] == "ops"


def test_deferral_day_uses_emission_clock_not_old_sync_start(store):
    midnight = (int(NOW) // 86400 + 1) * 86400
    store.clock = lambda: midnight + 1
    cap = {**OPEN(), "ok_usd": False}
    # A slow pass started yesterday; a second pass starts today. Both emit today.
    sh.file_fix(FP, store=store, runner=Board(), now=midnight - 1, budget_fn=lambda *a: cap)
    sh.file_fix(FP, store=store, runner=Board(), now=midnight + 1, budget_fn=lambda *a: cap)
    rows = events(store, "selfheal_fix_deferred")
    assert len(rows) == 1
    assert rows[0]["utc_day"] == sh._utc_day(rows[0]["ts"])


@pytest.mark.parametrize("default_store", [False, True])
def test_concurrent_fix_denials_log_once_across_processes(store, default_store):
    code = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import self_heal as sh
from core import Store
cap = {"available": True, "ok_usd": False, "ok_running": True, "why": "daily cap"}
def no_create(*a, **kw):
    raise AssertionError("denied admission must not call kanban")
sh.coach_root = lambda: Path(sys.argv[2])
store = None if sys.argv[5] == "True" else Store(Path(sys.argv[2]))
print(sh.file_fix(sys.argv[3], store=store, now=float(sys.argv[4]),
                  runner=no_create, budget_fn=lambda *a: cap)["action"])
'''
    assert sh.__file__ is not None
    argv = [sys.executable, "-c", code, str(Path(sh.__file__).parent), str(store.root), FP, str(NOW), str(default_store)]
    children = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
    for child in children:
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
        assert out.strip() == "deferred"
    assert len(events(store, "selfheal_fix_deferred")) == 1


def test_fix_deferred_once_per_utc_day_fingerprint_reason(store):
    board = Board()
    cap = {**OPEN(), "ok_usd": False, "why": "spend 201 >= cap 200"}
    for n in range(3):
        # Fresh store/lane objects model independent sync processes.
        fresh = core.Store(store.root, clock=lambda: NOW + n)
        result = sh.file_fix(FP, store=fresh, runner=board, now=NOW + n, budget_fn=lambda *a: cap)
        assert result["action"] == "deferred"
    assert len(events(store, "selfheal_fix_deferred")) == 1
    store.clock = lambda: NOW + 3
    cap["why"] = "spend 202 >= cap 200"  # changing metrics are not a new reason
    sh.file_fix(FP, store=store, runner=board, now=NOW + 3, budget_fn=lambda *a: cap)
    assert len(events(store, "selfheal_fix_deferred")) == 1
    busy = {**OPEN(), "ok_running": False}
    sh.file_fix(FP, store=store, runner=board, now=NOW + 4, budget_fn=lambda *a: busy)
    sh.file_fix(FP, store=store, runner=board, now=NOW + 5, budget_fn=lambda *a: cap)
    assert len(events(store, "selfheal_fix_deferred")) == 2
    other = "agent-abcdef1234"
    sh.LaneState(store.root).update(other, status="proposed", component="agent")
    sh.file_fix(other, store=store, runner=board, now=NOW + 6, budget_fn=lambda *a: cap)
    assert len(events(store, "selfheal_fix_deferred")) == 3
    midnight = (int(NOW) // 86400 + 1) * 86400
    store.clock = lambda: midnight
    sh.file_fix(FP, store=store, runner=board, now=midnight, budget_fn=lambda *a: cap)
    sh.file_fix(FP, store=store, runner=board, now=midnight + 1, budget_fn=lambda *a: cap)
    assert len(events(store, "selfheal_fix_deferred")) == 4
    assert not board.creates()
    assert sh.LaneState(store.root).get(FP)["status"] == "proposed"
    assert sh.file_fix(FP, store=store, runner=board, now=midnight + 2, budget_fn=OPEN)["action"] == "filed"


class _ReceiptDuringLookup:
    """Board whose `show` of the verify card is when the verifier's receipt lands (the production race: reconcile
    iterates a lane snapshot read at pass start, and card lookups for ~1,000 lanes take minutes)."""

    def __init__(self, board, store, kind, at):
        self.board, self.store, self.kind, self.at, self.done = board, store, kind, at, False

    def __call__(self, argv, **kw):
        if "show" in argv and "t_verify01" in argv and not self.done:
            self.done = True
            sh.record_event(self.kind, FP, card="t_abcdef01", note="abcdef1234", store=self.store, now=self.at)
        return self.board(argv, **kw)


@pytest.mark.parametrize("kind", ["fixed", "not_defect"])
def test_receipt_recorded_during_a_reconcile_pass_is_not_overwritten_by_stale(store, monkeypatch, kind):
    monkeypatch.setattr(sh, "budget", OPEN)
    lane = sh.LaneState(store.root)
    lane.update(FP, status="approved", fix_card="t_abcdef01", verify_card="t_verify01")
    board = _ReceiptDuringLookup(Board(status={"t_abcdef01": "done", "t_verify01": "done"}), store, kind, NOW + 5)
    sh.reconcile(store, runner=board, now=NOW)
    assert board.done
    assert lane.get(FP)["status"] == kind
    assert not events(store, "selfheal_stale")


def test_done_verifier_without_any_receipt_still_goes_stale(store, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    lane = sh.LaneState(store.root)
    lane.update(FP, status="approved", fix_card="t_abcdef01", verify_card="t_verify01")
    sh.reconcile(store, runner=Board(status={"t_abcdef01": "done", "t_verify01": "done"}), now=NOW)
    assert lane.get(FP)["status"] == "stale"


def test_lane_transition_is_compare_and_set(store):
    lane = sh.LaneState(store.root)
    lane.update(FP, status="fixed", fixed_at=NOW)
    assert lane.transition(FP, {"status": "approved"}, status="stale", closed_at=NOW) is None
    assert lane.get(FP)["status"] == "fixed" and "closed_at" not in lane.get(FP)
    assert lane.transition(FP, {"status": "fixed"}, status="reverted")["status"] == "reverted"


def test_concurrent_lane_writers_do_not_drop_each_others_updates(store):
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import self_heal as sh; from pathlib import Path\n"
            "lane = sh.LaneState(Path(sys.argv[2]))\n"
            "for i in range(25): lane.update(f'fp-{sys.argv[3]}-{i}', status='filed')\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(Path(sh.__file__).parent), str(store.root), str(n)])
             for n in range(6)]
    assert [p.wait(timeout=120) for p in procs] == [0] * 6
    assert sum(k.startswith('fp-') for k in sh.LaneState(store.root).read()) == 150

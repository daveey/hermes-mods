"""Self-heal fix cards reach the worker pool (kanban-12ee0e4b65).

Every fix card used to declare host_repo and quote a proposal full of hub words (the hub, its kanban.db, ssh, cron),
so core's portable_reason kept all of them on the hub's 7 slots. A default-board fix is now a portable FIX card
(terminal/file/git, the proposal fenced as evidence) plus a VERIFY child that stays on the hub for the live check
and the lane event. These tests build both from the real templates and judge them with core's real placement check.
"""
import importlib.util
import json
import os
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


sh = load("coach_self_heal_fix_placement_test", ROOT / "self_heal.py")
core = load("coach_core_fix_placement_test", ROOT / "core.py")

NOW = 1794218000.0
FP = "kanban-0123456789"
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "available": True, "why": ""}
# What investigations really write: 233 of 259 proposals named the hub or its state while saying what happened.
PROPOSAL = """Cards wait on the hub although worker-1 is idle

**What happened**
On the hub, `sqlite3 ~/.hermes/kanban.db` showed 7 running and 20 ready; the hub's cron job and the ssh relay
kept retrying, and hermes-sync logged it too. The Desktop chat on the MacBook showed the stall.

**Plan**
Change plugins/coach/self_heal.py and add a regression test.
"""
POOL_CFG = {"worker_pools": {"hub": ["worker-1"]}}


def placement():
    """Core's real placement module (the tree under HERMES_AGENT_TREE, else the installed checkout)."""
    tree = Path(os.environ.get("HERMES_AGENT_TREE") or Path.home() / ".hermes" / "hermes-agent")
    sys.path.insert(0, str(tree))
    try:
        from hermes_cli import kanban_placement
    except Exception:
        pytest.skip(f"hermes-agent not importable from {tree}")
    finally:
        sys.path.remove(str(tree))
    if not hasattr(kanban_placement, "without_evidence"):
        pytest.skip(f"hermes-agent at {tree} predates the placement evidence fence (core d1de9c90794)")
    return kanban_placement


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_SELFHEAL_NO_EXEC", "1")
    monkeypatch.setattr(sh, "coach_root", lambda: tmp_path / "coach")
    s = core.Store(tmp_path / "coach")
    (tmp_path / "coach" / "selfheal" / FP).mkdir(parents=True)
    (tmp_path / "coach" / "selfheal" / FP / "proposal.md").write_text(PROPOSAL)
    sh.LaneState(s.root).update(FP, status="proposed", component="kanban", symptom="cards wait although a worker is idle",
                                card="t_0000inv0")
    return s


class Board:
    """Fake `hermes kanban`: create returns sequential ids (or fails for the `fail` call numbers); show -> status."""

    def __init__(self, fail=(), status=None):
        self.calls, self.n, self.fail, self.status = [], 0, set(fail), dict(status or {})

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append(list(argv))
        if "create" in argv:
            self.n += 1
            if self.n in self.fail:
                return SimpleNamespace(returncode=1, stdout="", stderr="refused")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if "show" in argv:
            card = argv[argv.index("show") + 1]
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": self.status.get(card, "ready")}}),
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def fields(argv):
    """The create's placement-relevant fields, as core's portable_reason reads them."""
    def many(flag):
        return [argv[i + 1] for i, a in enumerate(argv) if a == flag]
    return {"title": argv[argv.index("create") + 1], "body": argv[argv.index("--body") + 1],
            "assignee": argv[argv.index("--assignee") + 1], "requires": many("--requires"),
            "skills": many("--skill"), "parents": many("--parent")}


def test_fix_card_from_the_real_template_is_portable(store):
    kp = placement()
    b = Board()
    r = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert r["action"] == "filed"
    fix = fields(b.creates()[0])
    assert fix["requires"] == ["terminal", "file", "git"]
    assert "On the hub" in fix["body"]  # the evidence is still there for the worker to read...
    assert kp.portable_reason(fix, POOL_CFG) is None  # ...but no longer pins the card to the hub
    assert kp.decide(fix, cfg=POOL_CFG, self_gw="hub").get("placement_policy") == "worker-first-v1"


def test_fix_card_work_text_never_tells_a_worker_to_write_the_lane(store):
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    body = fields(b.creates()[0])["body"]
    assert sh.SELF_EVENT not in body  # an `event` run on a worker writes the worker's own coach store


def test_verify_child_stays_on_the_hub_and_records_for_the_fix_card(store):
    kp = placement()
    b = Board()
    r = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert len(b.creates()) == 2 and r["verify_card"] == "t_00000002"
    verify = fields(b.creates()[1])
    assert verify["parents"] == ["t_00000001"]
    assert "host_repo" in verify["requires"]
    assert kp.portable_reason(verify, POOL_CFG) is not None
    assert f"{sh.SELF_EVENT} fixed {FP} --card t_00000001" in verify["body"]
    assert f"{sh.SELF_EVENT} not_defect {FP} --card t_00000001" in verify["body"]
    st = sh.LaneState(store.root).get(FP)
    assert (st["status"], st["fix_card"], st["verify_card"]) == ("approved", "t_00000001", "t_00000002")


def test_investigation_card_stays_on_the_hub():
    kp = placement()
    inv = sh.card_args({"component": "kanban", "symptom": "x", "fingerprint": FP}, "k")
    assert kp.portable_reason({**inv, "requires": inv["requires"]}, POOL_CFG) == "uncarried requirement"


def test_bb_and_named_board_fixes_keep_one_hub_card():
    assert not sh.split_fix({"component": "bb"}, "bb")
    assert not sh.split_fix({"component": "kanban"}, "ops")
    assert sh.split_fix({"component": "kanban"}, None) and sh.split_fix({"component": "plugin"}, "default")


def test_a_failed_verify_create_is_retried_alone(store):
    b = Board(fail={2})
    r = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert r["action"] == "filed" and r["verify_card"] is None
    errs = [json.loads(x) for x in (Path(store.root) / "log.jsonl").read_text().splitlines()
            if '"selfheal_file_error"' in x]
    assert errs and errs[-1]["stage"] == "verify"
    again = sh.file_fix(FP, store=store, runner=b, now=NOW + 60, budget_fn=OPEN)
    assert again["action"] == "skip"
    third = fields(b.creates()[2])
    assert third["parents"] == ["t_00000001"] and third["title"].startswith(sh.VERIFY_TITLE)
    assert sh.LaneState(store.root).get(FP)["verify_card"] == "t_00000003"


@pytest.mark.parametrize("fix, verify, stale", [
    ("done", "todo", False),       # fix landed, verify still to run: not stale
    ("done", "ready", False),
    ("done", "done", True),        # verify ended without `event fixed`: stale, refile
    ("archived", "todo", True),    # fix card abandoned: stale as before
])
def test_reconcile_judges_a_split_fix_by_its_verify_child(store, fix, verify, stale):
    sh.LaneState(store.root).update(FP, status="approved", fix_card="t_f", verify_card="t_v")
    b = Board(status={"t_f": fix, "t_v": verify})
    out = sh.reconcile(store=store, runner=b, now=NOW)
    assert (sh.LaneState(store.root).get(FP)["status"] == "stale") is stale, out

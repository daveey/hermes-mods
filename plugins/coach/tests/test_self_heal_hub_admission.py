"""Self-heal cards get past core's hub admission (t_01a1cfa3b3805315).

Core's hub admission (hermes_cli/kanban_hub_admission_placement.apply) refuses an explicit `machine:hub` pin unless a
Sonnet judge admits an allowlisted hub-only need. Every self-heal investigation, deploy check and lane card was
created with `--runs-on machine:<home gateway>`; on the hub the judge denied each one (their gateway steps are reads
and hub-admin writes), so from 11:22 UTC 2026-11-12 the lane filed nothing and paid one judge call per pending row
per pass. On a remote-reachable gateway these cards now go to the pool (the VERIFY child's labels and its
hub-read/hub-admin route); a named board and every other gateway keep the home pin. A placement refusal stops the
pending pass at the first row and files one lane card.

These tests run each producer's real argv through core's real admission apply(), with a judge that fails the test if
it is ever consulted.
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


sh = load("coach_self_heal_hub_admission_test", ROOT / "self_heal.py")
core = load("coach_core_hub_admission_test", ROOT / "core.py")

NOW = 1794484000.0
FP = "kanban-0123456789"
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "available": True, "why": ""}
POOL_CFG = {"worker_pools": {"hub": ["worker-1"]}}
_WORKER = ["capability:coordination-read", "capability:core-ship", "capability:file", "capability:git",
           "capability:hub-read", "capability:hub-write", "capability:kanban", "capability:terminal",
           "capability:token-broker", "linux", "profile:default"]
REGISTRY = {"machines": {
    "hub": {"state": "active", "labels": ["capability:file", "capability:git", "capability:kanban",
                                          "capability:terminal", "linux", "profile:default"]},
    "worker-1": {"state": "active", "pickup": True, "labels": _WORKER}}}
# What core prints for a refused create (cli_preflight --json): the real refusal shape seen in the lane's log.
DENIED = json.dumps({"ok": False, "created": False, "code": "hub_admission_denied",
                     "error": "A pool worker can read the coach store over ssh or hub-admin.",
                     "hub_admission": {"admit": False, "need": None, "evidence": "", "why": "no hub-only need",
                                       "model": "claude-sonnet-5-5", "skill_sha": "b18042a2", "cached": False,
                                       "forced": False}}, indent=2)


def admission():
    """Core's real admission module, or skip (core-free CI)."""
    tree = Path(os.environ.get("HERMES_AGENT_TREE") or Path.home() / ".hermes" / "hermes-agent")
    sys.path.insert(0, str(tree))
    try:
        from hermes_cli import kanban_hub_admission_placement as hap
    except Exception:
        pytest.skip(f"hermes-agent at {tree} predates hub admission")
    finally:
        sys.path.remove(str(tree))
    return hap


@pytest.fixture
def gate(monkeypatch):
    """apply() against a fixture pool; the judge fails the test when consulted, unless a test sets a verdict."""
    hap = admission()
    from hermes_cli import kanban_hub_admission as judge
    from hermes_cli import kanban_worker_node as wn
    monkeypatch.setattr(wn, "registry", lambda: REGISTRY)
    state = SimpleNamespace(calls=[], verdict=None)

    def admit(fields, *, hub_reason=None):
        state.calls.append(fields.get("title"))
        if state.verdict is None:
            raise AssertionError(f"hub admission judge consulted for {fields.get('title')!r}")
        return state.verdict
    monkeypatch.setattr(judge, "admit", admit)
    state.judge, state.hap = judge, hap

    def decide(argv):
        f = fields(argv)
        runs_on = f["runs_on"].split(",") if f["runs_on"] else None
        args = {**f, "runs_on": runs_on, "board": f.get("board")}
        return hap.apply(args, {"where": "local", "why": "cli create"}, "hub", POOL_CFG)
    state.decide = decide
    return state


@pytest.fixture
def store(tmp_path, monkeypatch, board_identity):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_SELFHEAL_NO_EXEC", "1")
    monkeypatch.setattr(sh, "coach_root", lambda: tmp_path / "coach")
    board_identity.set(gateway="hub")
    return core.Store(tmp_path / "coach")


class Board:
    """Fake `hermes kanban`: creates get sequential ids, or core's refusal for the call numbers in ``deny``."""

    def __init__(self, deny=()):
        self.calls, self.n, self.deny = [], 0, set(deny)

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append(list(argv))
        if "create" in argv:
            self.n += 1
            if self.n in self.deny or "*" in self.deny:
                return SimpleNamespace(returncode=2, stdout=DENIED, stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if "show" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": "ready"}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def fields(argv):
    def many(flag):
        return [argv[i + 1] for i, a in enumerate(argv) if a == flag]
    board = many("--board")
    return {"title": argv[argv.index("create") + 1], "body": argv[argv.index("--body") + 1],
            "assignee": argv[argv.index("--assignee") + 1], "requires": many("--requires"),
            "runs_on": (many("--runs-on") or [None])[0], "board": board[0] if board else None}


def defect(component="kanban", symptom="cards wait on the hub although a worker is idle"):
    return {"fingerprint": FP, "component": component, "symptom": symptom, "source": "signal",
            "host": "hub-host", "at": NOW - 60, "quote": "q", "kind": "signal"}


def investigation_argv():
    return sh.kanban_argv(sh.card_args(defect(), "k"))


# ── every producer's argv passes admission without a judge call ─────────────────────────────────────────────────
def test_hub_investigation_goes_to_the_pool_not_machine_hub(store, gate):
    argv = investigation_argv()
    f = fields(argv)
    assert f["runs_on"] == ",".join(sh.POOL_LABELS) and argv.count("--runs-on") == 1
    assert "machine:" not in f["runs_on"]
    out = gate.decide(argv)
    assert out.get("where") != "refuse" and "hub_admission" not in out, out
    assert gate.calls == []  # pool labels a worker carries are not a hub slot: no Sonnet call per card


def test_old_machine_hub_pin_is_refused_by_the_same_gate(store, gate):
    """RED proof of the incident: the argv the lane used to build is refused when the judge denies it."""
    argv = sh.kanban_argv({**sh.card_args(defect(), "k"), "home_pin": True, "runs_on": []})
    assert fields(argv)["runs_on"] == "machine:hub"
    gate.verdict = gate.judge.Verdict(why="no allowlisted hub-only need")
    out = gate.decide(argv)
    assert out["where"] == "refuse" and gate.calls


def test_hub_lane_and_deploy_cards_go_to_the_pool(store, gate):
    b = Board()
    sh._lane_card("self-heal lane: x", f"Body.\n{sh.home_pin_note(sh.PIN_LANE)}\n\nmore", "key-1", b)
    dec = {"label": "macbook", "sha": "a" * 40, "last_sync": NOW - 86400, "at": NOW, "deployed_at": None,
           "overdue": True, "key": "undeployed-k", "why": "w"}
    sh._file_undeployed(defect(), FP, {"fix_card": "t_f", "fixed_at": NOW - 90000}, dec, store, "live", b, NOW,
                        {"mode": "live"}, OPEN)
    creates = b.creates()
    assert len(creates) == 2, creates
    for argv in creates:
        f = fields(argv)
        assert f["runs_on"] == ",".join(sh.POOL_LABELS), f["title"]
        assert gate.decide(argv).get("where") != "refuse"
        assert "Pinned to the gateway that filed it" not in f["body"] and sh.REMOTE_NOTE in f["body"]
    assert gate.calls == []


def test_hub_split_fix_and_verify_still_pass_admission(store, gate):
    lane = sh.LaneState(store.root)
    lane.update(FP, status="proposed", component="kanban", symptom="s", card="t_inv")
    (store.root / "selfheal" / FP).mkdir(parents=True)
    (store.root / "selfheal" / FP / "proposal.md").write_text("Title\n\nPlan: change self_heal.py\n")
    b = Board()
    assert sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)["action"] == "filed"
    for argv in b.creates():
        assert "machine:" not in (fields(argv)["runs_on"] or "")
        # the FIX has no runs_on: on the hub it is judged and, denied, routed to the pool (never refused)
        gate.verdict = gate.judge.Verdict(why="repo work")
        assert gate.decide(argv).get("where") != "refuse"


# ── the remote brief: every gateway step goes through hub-read / hub-admin ──────────────────────────────────────
def test_hub_investigation_brief_records_on_the_gateway(store):
    body = fields(investigation_argv())["body"]
    assert sh.REMOTE_NOTE in body and "Pinned to the gateway that filed it" not in body
    for kind in ("duplicate", "not_defect", "proposed"):
        assert f"`hub-admin '{sh.SELF_EVENT} {kind} {FP}" in body, kind
    assert sh.SELF_EVENT not in body.replace(f"hub-admin '{sh.SELF_EVENT}", "")  # no bare local event
    stage = f".hermes/coach/selfheal/{FP}/proposal.md"
    assert f"base64 -d > {stage}" in body and f"head -n 3 {stage}" in body
    assert "write the proposal to `proposal.md` here" in body


def test_other_gateways_and_named_boards_keep_the_home_pin(store, board_identity):
    board_identity.set(gateway="gw-board-a")
    f = fields(investigation_argv())
    assert f["runs_on"] == "machine:gw-board-a" and "hub-admin" not in f["body"]
    board_identity.set(gateway="hub")
    bb = sh.card_args(defect(component="bb"), "k", cfg={"bb_board": "bb"})
    assert bb["board"] == "bb" and bb["home_pin"] is True  # the bb board is not on the pool


def test_unresolved_identity_never_files_a_portable_investigation(store, board_identity):
    board_identity.set(error="enrollment unreadable")
    args = sh.card_args(defect(), "k")
    assert args["home_pin"] is True and not args["runs_on"]
    rc, _, err = sh._run_card(args, Board())
    assert rc == 1 and "home pin unresolved" in err


# ── a placement refusal stops the pass and is reported once ────────────────────────────────────────────────────
def _pend(store, n):
    for i in range(n):
        d = {**defect(), "fingerprint": f"kanban-{i:010x}"}
        sh._defer_investigation(d, {"action": "deferred", "retry_at": sh._iso(NOW - 10)}, store)


def _events(store, name):
    return [json.loads(x) for x in (Path(store.root) / "log.jsonl").read_text().splitlines()
            if f'"{name}"' in x]


def test_a_placement_refusal_stops_the_pending_pass(store):
    _pend(store, 3)
    calls = []
    out = sh.retry_deferred(store, now=NOW, filer=lambda d, **k: calls.append(d) or
                            {"action": "error", "why": "refused", "placement_refused": True})
    assert len(calls) == 1 and len(out) == 1
    (stop,) = _events(store, "selfheal_pass_stopped")
    assert stop["why"] == "placement_refused" and stop["left"] == 2
    # the rows stay pending for the next pass
    seen = []
    sh.retry_deferred(store, now=NOW + 1, filer=lambda d, **k: seen.append(d) or {"action": "filed"})
    assert len(seen) == 3


def test_refused_create_is_classified_and_files_one_lane_card(store, monkeypatch):
    monkeypatch.setattr(sh, "is_hub", lambda cfg=None: True)
    monkeypatch.setattr(sh, "host_name", lambda: "hub-host")
    b = Board(deny={1})
    r = sh.file_defect(defect(), store=store, m="live", runner=b, now=NOW, cfg={}, budget_fn=OPEN,
                       owner_fn=lambda *a, **k: None, sig_owner_fn=lambda *a, **k: None)
    assert r["action"] == "error" and r["placement_refused"] is True
    (err,) = _events(store, "selfheal_file_error")
    assert err["code"] == "placement_refused"
    (card,) = _events(store, "selfheal_placement_card")
    assert card["action"] == "filed"
    alert = fields(b.creates()[1])
    assert alert["title"] == "self-heal lane: card creates refused by hub admission"
    assert alert["runs_on"] == ",".join(sh.POOL_LABELS)  # the alert itself must not need the hub
    # an open alert card is not filed again
    b2 = Board(deny={1})
    sh.file_defect({**defect(), "fingerprint": "kanban-00000000ff"}, store=store, m="live", runner=b2, now=NOW + 60,
                   cfg={}, budget_fn=OPEN, owner_fn=lambda *a, **k: None, sig_owner_fn=lambda *a, **k: None)
    assert len(b2.creates()) == 1


@pytest.mark.parametrize("text, refused", [
    (DENIED, True),
    ("kanban: hub_admission_denied: no hub-only need (no card was created)", True),
    ('...,\n    "model": "claude-sonnet-5-5"', False),
    ("kanban board 'bb' does not exist", False),
    ("refused", False),
])
def test_placement_refusal_classifier(text, refused):
    assert sh.placement_refused(text) is refused

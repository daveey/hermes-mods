"""Every coach producer's home-bound card gets past core's hub admission (t_01a1cfb30dc5d3da).

Core's hub admission (hermes_cli/kanban_hub_admission_placement.apply) refuses an explicit `machine:hub` pin unless a
Sonnet judge admits an allowlisted hub-only need (skill hub-admission-test). The self-heal lane's cards moved to the
pool in b9e5118a; the other producers (backend_timeouts, probe_token_watch x2, restart_notice_config_watch,
stranded_sqlite_watch, withheld_notice_watch) still built `--runs-on machine:<home gateway>` through
placement_evidence.home_pin_argv(), so any of them firing on the hub exited 2 (hub_admission_denied). They now share
placement_evidence.home_route(): on a remote-reachable gateway the pool labels and a brief whose gateway steps go
through hub-read / hub-admin; elsewhere the machine pin, unchanged.

Each test runs the producer's real filing function with a recording runner, parses the emitted argv with core's own
argparse tree, and runs it through core's real apply() with a judge that fails the test if it is ever consulted.
"""
import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime
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


pe = load("coach_placement_evidence_hubadm_test", ROOT / "placement_evidence.py")
btw = load("coach_backend_timeouts_hubadm_test", ROOT / "backend_timeouts.py")
ptw = load("coach_probe_token_hubadm_test", ROOT / "probe_token_watch.py")
rnw = load("coach_restart_notice_hubadm_test", ROOT / "restart_notice_config_watch.py")
ssw = load("coach_stranded_sqlite_hubadm_test", ROOT / "stranded_sqlite_watch.py")
wnw = load("coach_withheld_hubadm_test", ROOT / "withheld_notice_watch.py")
sh = load("coach_self_heal_hubadm_test", ROOT / "self_heal.py")

HOST = "host-a"
POOL_CFG = {"worker_pools": {"hub": ["worker-1"]}}
_WORKER = ["capability:coordination-read", "capability:core-ship", "capability:file", "capability:git",
           "capability:hub-read", "capability:hub-write", "capability:kanban", "capability:terminal",
           "capability:token-broker", "linux", "profile:default"]
REGISTRY = {"machines": {
    "hub": {"state": "active", "labels": ["capability:file", "capability:git", "capability:kanban",
                                          "capability:terminal", "linux", "profile:default"]},
    "worker-1": {"state": "active", "pickup": True, "labels": _WORKER}}}


def core_tree():
    tree = Path(os.environ.get("HERMES_AGENT_TREE") or Path.home() / ".hermes" / "hermes-agent")
    sys.path.insert(0, str(tree))
    try:
        from hermes_cli import kanban_hub_admission_placement as hap
        from hermes_cli import kanban_parser
    except Exception:
        pytest.skip(f"hermes-agent at {tree} predates hub admission")
    finally:
        sys.path.remove(str(tree))
    return hap, kanban_parser


@pytest.fixture
def gate(monkeypatch, board_identity):
    """apply() for the hub against a fixture pool; the judge fails the test when consulted."""
    hap, parser = core_tree()
    from hermes_cli import kanban_hub_admission as judge
    from hermes_cli import kanban_worker_node as wn
    monkeypatch.setattr(wn, "registry", lambda: REGISTRY)
    board_identity.set(gateway="hub")
    state = SimpleNamespace(calls=[], verdict=None, board_identity=board_identity)

    def admit(fields, *, hub_reason=None):
        state.calls.append(fields.get("title"))
        if state.verdict is None:
            raise AssertionError(f"hub admission judge consulted for {fields.get('title')!r}")
        return state.verdict
    monkeypatch.setattr(judge, "admit", admit)
    state.judge = judge
    root = argparse.ArgumentParser()
    parser.build_parser(root.add_subparsers(dest="cmd"))

    def decide(argv, body):
        a = root.parse_args(list(argv[1:]))
        runs_on = a.runs_on.split(",") if getattr(a, "runs_on", None) else None
        args = {"title": a.title, "body": body, "assignee": a.assignee, "requires": list(a.requires or []),
                "runs_on": runs_on, "board": getattr(a, "board", None), "hub_reason": getattr(a, "hub_reason", None)}
        return hap.apply(args, {"where": "local", "why": "cli create"}, "hub", POOL_CFG)
    state.decide = decide
    return state


class Runner:
    """Stands in for `hermes kanban`: records every create (argv + stdin body), returns a new id."""

    def __init__(self):
        self.creates = []

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None, **_):
        if "create" in argv:
            self.creates.append((list(argv), input or ""))
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{len(self.creates):08x}",
                                                                    "created_at": 1e12}), stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": "ready"}}), stderr="")


# ── each producer, driven through its real filing function ─────────────────────────────────────────────────────────
def _backend_timeouts(tmp_path, run):
    rep = {"count": 12, "first": "t0", "last": "t1", "by_path": {"/api/x": 12}, "awake_count": 12}
    btw.file_card(tmp_path, HOST, datetime(2026, 11, 11), rep, {"loadavg": [1, 2, 3], "backend_ps": ["p"]}, 5, 30, run)


def _probe_token_card(tmp_path, run):
    ptw.file_card(tmp_path, {"session": "sess1", "profile": "default", "source": "tui", "ts": 1794218000.0,
                             "title": "t", "first": "f", "host": HOST, "evidence": "e", "evidence_lines": ["e"]}, run)


def _probe_token_leftovers(tmp_path, run):
    ptw.file_leftovers(tmp_path, [{"session": "sess2", "profile": "default", "source": "tui", "ts": 1794218000.0,
                                   "title": "t", "first": "f", "host": HOST, "evidence": "e"}], run)


def _restart_notice(tmp_path, run):
    rnw.file_cards([{"profile": "default", "platform": "slack", "home_channel": "C1", "home_channel_name": "n",
                     "home_source": "cfg", "gateway_restart_notification": None, "config": "~/.hermes/config.yaml"}],
                   HOST, run)


def _stranded_sqlite(tmp_path, run):
    ssw.file_cards([{"pid": 11, "starttime": 22, "cmdline": "python serve", "db": "~/.hermes/kanban.db",
                     "sidecars": [{"path": "~/.hermes/kanban.db-wal", "fd": 3, "deleted_ctime": None}]}], HOST, run)


def _withheld_notice(tmp_path, run):
    wnw.file_card(tmp_path, HOST, datetime(2026, 11, 11),
                  [{"ts": 1794218000.0, "session": "s1", "id": 7, "text": "x"}], run)


PRODUCERS = {"backend_timeouts": _backend_timeouts, "probe_token_card": _probe_token_card,
             "probe_token_leftovers": _probe_token_leftovers, "restart_notice_config": _restart_notice,
             "stranded_sqlite": _stranded_sqlite, "withheld_notice": _withheld_notice}


def _one_create(name, tmp_path):
    run = Runner()
    PRODUCERS[name](tmp_path, run)
    assert len(run.creates) == 1, (name, run.creates)
    return run.creates[0]


@pytest.mark.parametrize("name", sorted(PRODUCERS))
def test_hub_card_goes_to_the_pool_without_a_judge_call(name, gate, tmp_path):
    argv, body = _one_create(name, tmp_path)
    runs_on = argv[argv.index("--runs-on") + 1]
    assert runs_on == ",".join(pe.POOL_LABELS) and argv.count("--runs-on") == 1, (name, runs_on)
    assert "machine:" not in runs_on
    out = gate.decide(argv, body)
    assert out.get("where") != "refuse" and "hub_admission" not in out, (name, out)
    assert gate.calls == []  # pool labels a worker carries are not a hub slot
    # the brief tells the pool worker where the state is, and never claims the card is pinned here
    assert pe.REMOTE_NOTE in body and pe.HOME_PIN_PREFIX not in body, name


@pytest.mark.parametrize("name", sorted(PRODUCERS))
def test_other_gateways_keep_the_home_pin(name, gate, tmp_path):
    gate.board_identity.set(gateway="gw-board-a")
    argv, body = _one_create(name, tmp_path)
    assert argv[argv.index("--runs-on") + 1] == "machine:gw-board-a", name
    assert pe.HOME_PIN_PREFIX in body and pe.REMOTE_NOTE not in body, name


@pytest.mark.parametrize("name", sorted(PRODUCERS))
def test_unresolved_identity_never_files_a_portable_card(name, gate, tmp_path):
    gate.board_identity.set(error="enrollment unreadable")
    run = Runner()
    PRODUCERS[name](tmp_path, run)
    assert run.creates == [], name


def test_old_machine_hub_pin_is_refused_by_the_same_gate(gate):
    """RED proof of the incident: the argv the producers used to build is refused when the judge denies it."""
    argv, body = rnw.create_route(HOST, {"profile": "default", "platform": "slack", "home_channel": "C1",
                                         "home_channel_name": "n", "home_source": "cfg",
                                         "gateway_restart_notification": None, "config": "c"})
    old = list(argv)
    old[old.index("--runs-on") + 1] = "machine:hub"
    gate.verdict = gate.judge.Verdict(why="no allowlisted hub-only need")
    assert gate.decide(old, body)["where"] == "refuse" and gate.calls


# ── self-heal bb cards: a named board on the hub is judged and denied; the hub files them on the default board ────
def _bb_defect():
    return {"fingerprint": "bb-0123456789", "component": "bb", "symptom": "the BB app shows a stale feed",
            "source": "signal", "host": "hub-host", "at": 1794484000.0, "quote": "q", "kind": "signal"}


@pytest.fixture
def store(tmp_path, monkeypatch, gate):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_SELFHEAL_NO_EXEC", "1")
    monkeypatch.setattr(sh, "coach_root", lambda: tmp_path / "coach")
    monkeypatch.setattr(sh, "lane_config", lambda: {"bb_board": "bb"})
    core = load("coach_core_hubadm_test", ROOT / "core.py")
    return core.Store(tmp_path / "coach")


def test_hub_bb_investigation_goes_to_the_pool_on_the_default_board(gate, store):
    args = sh.card_args(_bb_defect(), "k", cfg={"bb_board": "bb"})
    assert args["board"] is None and args["skills"] == list(sh.BB_SKILLS)
    argv = sh.kanban_argv(args)
    assert "--board" not in argv and argv[argv.index("--runs-on") + 1] == ",".join(pe.POOL_LABELS)
    body = argv[argv.index("--body") + 1]
    out = gate.decide(argv, body)
    assert out.get("where") != "refuse" and gate.calls == [], out


def test_hub_bb_named_board_card_is_refused_red_proof(gate, store):
    """What the hub used to file for a bb defect: the bb board, judged (structural) and denied."""
    argv = sh.kanban_argv({**sh.card_args(_bb_defect(), "k", cfg={"bb_board": "bb"}), "board": "bb",
                           "home_pin": True, "runs_on": []})
    gate.verdict = gate.judge.Verdict(why="no allowlisted hub-only need")
    assert gate.decide(argv, argv[argv.index("--body") + 1])["where"] == "refuse" and gate.calls


def test_hub_bb_fix_goes_to_the_pool_on_the_default_board(gate, store):
    fp = "bb-0123456789"
    sh.LaneState(store.root).update(fp, status="proposed", component="bb", symptom="s", card="t_inv")
    (store.root / "selfheal" / fp).mkdir(parents=True)
    (store.root / "selfheal" / fp / "proposal.md").write_text("Title\n\nPlan: change the feed\n")
    run = Runner()
    run_cli = lambda argv, **k: run(argv, **k)  # noqa: E731
    open_budget = lambda board=None, now=None: {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True,  # noqa: E731
                                                "ok_running": True, "available": True, "why": ""}
    out = sh.file_fix(fp, store=store, runner=run_cli, now=1794484000.0, budget_fn=open_budget)
    assert out["action"] == "filed", out
    (argv, _), = run.creates
    assert "--board" not in argv and argv[argv.index("--runs-on") + 1] == ",".join(pe.POOL_LABELS)
    body = argv[argv.index("--body") + 1]
    assert sh.REMOTE_NOTE in body and gate.decide(argv, body).get("where") != "refuse" and gate.calls == []


def test_bb_board_stays_off_the_hub(gate):
    gate.board_identity.set(gateway="gw-board-a")
    args = sh.card_args(_bb_defect(), "k", cfg={"bb_board": "bb"})
    assert args["board"] == "bb" and args["home_pin"] is True

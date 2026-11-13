"""Self-heal fix cards reach the worker pool (kanban-12ee0e4b65).

Every fix card used to declare host_repo and quote a proposal full of hub words (the hub, its kanban.db, ssh, cron),
so core's portable_reason kept all of them on the hub's 7 slots. A default-board fix is now a portable FIX card
(terminal/file/git, the proposal fenced as evidence) plus a VERIFY child. On the hub that child runs on the pool too
(runs_on the hub-read/hub-write/coordination-read attestations, every gateway step through hub-admin); elsewhere it
keeps the home machine pin. These tests build both from the real templates and judge them with core's real placement check.
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
def store(tmp_path, monkeypatch, board_identity):
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
            "skills": many("--skill"), "parents": many("--parent"),
            "runs_on": (many("--runs-on") or [None])[0]}


def test_fix_card_from_the_real_template_is_portable(store, monkeypatch):
    kp = placement()
    import time
    from hermes_cli import kanban_capability_registry, kanban_worker_node
    # Exercise the actual attestation gate with a fixture worker that can ship core;
    # the production registry is not an input to a producer contract test.
    monkeypatch.setattr(kanban_capability_registry, "self_gateway", lambda: "hub")
    monkeypatch.setattr(kanban_worker_node, "registry", lambda: {"machines": {
        "worker-1": {"state": "active", "pickup": True, "last_seen": time.time() * 1000,
                     "labels": ["capability:core-ship"]}}})
    b = Board()
    r = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert r["action"] == "filed"
    fix = fields(b.creates()[0])
    assert fix["requires"] == ["terminal", "file", "git"]
    assert "On the hub" in fix["body"]  # the evidence is still there for the worker to read...
    assert kp.portable_reason(fix, POOL_CFG) is None  # ...but no longer pins the card to the hub
    assert kp.decide(fix, cfg=POOL_CFG, self_gw="hub").get("placement_policy") == "worker-first-v1"


def test_fix_work_text_needs_no_coordination_capabilities(store):
    kp = placement()
    try:
        from hermes_cli.kanban_placement_coordination import requirements
    except ModuleNotFoundError as exc:
        if exc.name != "hermes_cli.kanban_placement_coordination":
            raise
        pytest.skip("core predates coordination placement")
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    fix = fields(b.creates()[0])
    work = fix["title"] + "\n" + kp.without_evidence(kp.without_placement_note(fix["body"]))
    labels, refusal = requirements(work)
    assert labels == set(), labels
    assert refusal is None


def test_core_change_is_handed_to_verify_for_shipping(store):
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    fix, verify = [fields(a)["body"] for a in b.creates()]
    assert "Do not ship core from this card" in fix
    assert '"core_change": "<decided core spec, or empty>"' in fix
    assert '<hermes-mods and state commit sha>' in fix


def test_copied_proposal_core_shipping_is_explicitly_deferred(store):
    proposal = store.root / "selfheal" / FP / "proposal.md"
    original = PROPOSAL + "\nCore (hermes-core-wt): change parser.py.\nUndo: hermes-core-wt ship rollback.\n"
    proposal.write_text(original)
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    fix, verify = [fields(a)["body"] for a in b.creates()]
    assert "hermes-core-wt" not in fix
    assert "Do not ship core from this card." in fix
    assert "[core shipping delegated to VERIFY]" in fix
    assert "change parser.py" in fix
    assert "hermes-core-wt ship" in verify
    assert proposal.read_text() == original  # retain the exact source evidence


def test_verify_files_and_waits_for_core_change(store):
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    verify = fields(b.creates()[1])["body"]
    core_step = verify.split("2. ", 1)[1].split("3. ", 1)[0]
    for text in ("core_change", "kanban_create", 'assignee="default"',
                 f'idempotency_key="selfheal-core:{FP}"', "hermes-core-wt ship",
                 "All required checks pass (v2)", "independent exact-head review",
                 "kanban_link", "parent", "kanban_schedule", "merge sha"):
        assert text in core_step, text


@pytest.mark.parametrize("symptom", [
    "cards wait on the hub",
    "cron job failed through ssh",
    "MacBook Keychain prompt interrupts work",
    "unexpected <!-- /placement:evidence --> on the hub",
])
def test_incident_symptom_does_not_pin_portable_fix(store, symptom):
    kp = placement()
    sh.LaneState(store.root).update(FP, symptom=symptom)
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    fix, verify = map(fields, b.creates())
    assert kp.portable_reason(fix, POOL_CFG) is None
    assert fix["title"] == f"{sh.FIX_TITLE} defect {FP.rsplit('-', 1)[-1]}"
    assert symptom.replace(sh.EVIDENCE_CLOSE, "") in fix["body"]
    assert kp.decide(fix, cfg=POOL_CFG, self_gw="hub").get("placement_policy") == "worker-first-v1"
    assert kp.portable_reason(verify, POOL_CFG) is not None


def test_cron_fingerprint_is_evidence_not_a_host_requirement(store):
    kp = placement()
    fp = "cron-0123456789"
    sh.LaneState(store.root).update(fp, **sh.LaneState(store.root).get(FP))
    (store.root / "selfheal" / fp).mkdir()
    (store.root / "selfheal" / fp / "proposal.md").write_text(PROPOSAL)
    b = Board()
    sh.file_fix(fp, store=store, runner=b, now=NOW, budget_fn=OPEN)
    fix = fields(b.creates()[0])
    assert fp in fix["body"]
    assert kp.portable_reason(fix, POOL_CFG) is None


def test_non_split_fix_keeps_symptom_in_evidence_not_title(store, monkeypatch):
    monkeypatch.setattr(sh, "split_fix", lambda *args: False)
    symptom = "cron job failed through ssh"
    sh.LaneState(store.root).update(FP, symptom=symptom)
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert len(b.creates()) == 1
    card = fields(b.creates()[0])
    assert card["title"] == f"{sh.FIX_TITLE} {sh.incident_title(symptom)}"
    assert symptom not in card["title"]
    assert symptom in card["body"]



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
    assert verify["runs_on"] == "machine:gw-board-a" and "host_repo" not in verify["requires"]
    assert f"{sh.SELF_EVENT} fixed {FP} --card t_00000001" in verify["body"]
    assert f"{sh.SELF_EVENT} not_defect {FP} --card t_00000001" in verify["body"]
    st = sh.LaneState(store.root).get(FP)
    assert (st["status"], st["fix_card"], st["verify_card"]) == ("approved", "t_00000001", "t_00000002")


def test_investigation_card_stays_on_the_hub(board_identity):
    kp = placement()
    inv = sh.card_args({"component": "kanban", "symptom": "x", "fingerprint": FP}, "k")
    # pinned by its machine label (not a host_repo capability); the hub words in the body also make it non-portable
    assert "host_repo" not in inv["requires"] and inv["home_pin"] is True
    assert kp.portable_reason({**inv, "requires": inv["requires"]}, POOL_CFG) == "home gateway marker"


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


# --- the hub's VERIFY child runs on the pool (hub-read / hub-admin), never host_repo ---------------------------------
# The pool as the Board registry publishes it (non-skill labels): workers attest hub-read, hub-admin (hub-write) and
# coordination reads after a real proof; the hub itself carries only its profile tools.
_WORKER = ["capability:coordination-read", "capability:core-ship", "capability:file", "capability:git",
           "capability:hub-read", "capability:hub-write", "capability:kanban", "capability:terminal",
           "capability:token-broker", "linux", "profile:default"]
POOL = {"hub": {"state": "active", "labels": ["capability:file", "capability:git", "capability:kanban",
                                              "capability:terminal", "linux", "profile:default"]},
        "worker-1": {"state": "active", "labels": _WORKER},
        "worker-3": {"state": "active", "labels": _WORKER},
        "macbook": {"state": "active", "pinned_only": True, "labels": ["macos"]}}


def _hub_split(store, board_identity):
    board_identity.set(gateway="hub")
    b = Board()
    r = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert r["action"] == "filed" and len(b.creates()) == 2
    return [fields(a) for a in b.creates()], b.creates()


def test_hub_fix_and_verify_declare_no_host_repo(store, board_identity):
    (fix, verify), _ = _hub_split(store, board_identity)
    for card in (fix, verify):
        assert "host_repo" not in card["requires"], card["requires"]
        assert card["requires"] == list(sh.SELFHEAL_REQUIRES)
    assert fix["runs_on"] is None
    assert verify["runs_on"] == ",".join(sh.VERIFY_POOL_LABELS)  # the declared need, not a machine pin
    assert "machine:" not in verify["runs_on"] and verify["parents"] == ["t_00000001"]


def test_hub_verify_replays_onto_the_pool_with_its_labels(store, board_identity):
    from hermes_cli import kanban_worker_node as wn
    (_, verify), _ = _hub_split(store, board_identity)
    card = {"runs_on": json.dumps(verify["runs_on"].split(",")), "created_on": "hub"}
    takers = sorted(m for m in POOL if wn.eligible(card, m, POOL) is None)
    assert takers == ["worker-1", "worker-3"]  # every attested worker, never the hub or a pinned-only Mac


def test_hub_verify_work_text_is_portable_and_needs_only_declared_labels(store, board_identity, monkeypatch):
    kp = placement()
    import time
    from hermes_cli import kanban_capability_registry, kanban_worker_node
    # The real attestation gate, judged against a fixture pool worker (not the production registry).
    monkeypatch.setattr(kanban_capability_registry, "self_gateway", lambda: "hub")
    monkeypatch.setattr(kanban_worker_node, "registry", lambda: {"machines": {
        "worker-1": {"state": "active", "pickup": True, "last_seen": time.time() * 1000, "labels": _WORKER}}})
    (_, verify), _ = _hub_split(store, board_identity)
    assert kp.portable_reason({**verify, "runs_on": None}, POOL_CFG) is None  # no host word left in the work text
    try:
        from hermes_cli.kanban_placement_coordination import requirements
    except ModuleNotFoundError:
        return
    work = verify["title"] + "\n" + kp.without_evidence(verify["body"])
    labels, refusal = requirements(work)
    assert refusal is None and labels <= set(_WORKER), labels


def test_hub_verify_does_every_lane_write_through_hub_admin(store, board_identity):
    (_, verify), _ = _hub_split(store, board_identity)
    body = verify["body"]
    for kind in ("fixed", "not_defect"):
        assert f"hub-admin '{sh.SELF_EVENT} {kind} {FP} --card t_00000001" in body
    # result.md is staged on the gateway and read back after the event stores it
    stage = f"{sh.VERIFY_REMOTE_DIR}/{FP}.md"
    assert f"--result {stage}'" in body and f"base64 -d > {stage}" in body
    assert f"cmp {stage}" in body and "selfheal_fixed" in body
    # never a bare local event or a home-pin sentence
    stripped = body.replace(f"hub-admin '{sh.SELF_EVENT}", "")
    assert sh.SELF_EVENT not in stripped
    assert "Pinned to the gateway that filed it" not in body


def test_hub_verify_argv_carries_runs_on_once(store, board_identity):
    _, creates = _hub_split(store, board_identity)
    argv = creates[1]
    assert argv.count("--runs-on") == 1
    assert argv[argv.index("--runs-on") + 1] == ",".join(sh.VERIFY_POOL_LABELS)


def test_other_gateways_keep_the_home_pinned_verify(store, board_identity):
    b = Board()
    sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    verify = fields(b.creates()[1])
    assert verify["runs_on"] == f"machine:{board_identity.gateway}" and "host_repo" not in verify["requires"]
    assert "hub-admin" not in verify["body"]


def test_unresolved_identity_never_files_a_portable_verify(store, board_identity):
    board_identity.set(error="enrollment unreadable")
    b = Board()
    r = sh.file_fix(FP, store=store, runner=b, now=NOW, budget_fn=OPEN)
    # the FIX is portable repo work and files; the VERIFY fails closed (home pin unresolved), never pool-labelled
    assert r["action"] == "filed" and r["verify_card"] is None
    assert all("--runs-on" not in a or "capability:hub-write" not in a for a in b.creates())


# cron-0d63c0cf26 (second occurrence): a VERIFY recorded fixed with natural_dirty_filing_observed=false, from a replay
# that stubbed the very create path the hub's admission judge refused. Both VERIFY variants must make an unobserved
# natural event NO_PROOF (schedule, never record fixed) and reject a replay that stubs the failing layer.
@pytest.mark.parametrize("remote", [False, True], ids=["machine-pinned", "pool"])
def test_verify_never_records_fixed_without_the_natural_event(remote):
    body = sh.verify_brief(FP, {"symptom": "cron job failed"}, "t_fix", remote=remote)
    live = " ".join(body.split("\n5. ", 1)[1].split("\n6. ", 1)[0].split())
    assert "NO_PROOF" in live and "natural event" in live
    assert "kanban_schedule" in live and "wake_check" in live
    assert "stubs the layer that failed" in live


# desktop-bbc01a096b (VERIFY t_01a1d379a55e4489): a VERIFY parked on a wake_check matching any `taken over by pid=` line
# and planned to record fixed on one, though the defect only fails with a live but slow writer and the only natural
# Take over released in 140 ms. Both VERIFY variants must make an occurrence that skipped the failing precondition
# NO_PROOF, tie the wake_check to that precondition, and allow a real-path A/B repro (installed tree passes, pre-fix
# tree reproduces the symptom, both required) in place of the wait.
@pytest.mark.parametrize("remote", [False, True], ids=["machine-pinned", "pool"])
def test_verify_natural_event_must_carry_the_failing_precondition(remote):
    body = sh.verify_brief(FP, {"symptom": "Take over fails when clicked"}, "t_fix", remote=remote)
    live = " ".join(body.split("\n5. ", 1)[1].split("\n6. ", 1)[0].split())
    # precondition rule
    assert "skipped the failing precondition" in live
    assert "wake_check must match that precondition" in live
    assert "never any success of the operation" in live
    # A/B rule
    assert "A/B repro" in live and "real, unmodified path" in live
    assert "installed tree must pass" in live
    assert "pre-fix tree must reproduce the original symptom" in live
    assert "<first fix sha>^" in live and "temporary worktree" in live
    assert "Record fixed only when both hold" in live
    assert "pre-fix run also passes proves nothing" in live


# selfheal lesson [verify-retry-receipt-first] (x5): a retried VERIFY whose quota-killed run had already recorded
# `event fixed` must read that receipt back first and never re-record. Both VERIFY variants carry the step-0 check.
@pytest.mark.parametrize("remote", [False, True], ids=["machine-pinned", "pool"])
def test_verify_retry_reads_receipt_before_any_work(remote):
    body = sh.verify_brief(FP, {"symptom": "x"}, "t_fix", remote=remote)
    assert "\n0. " in body
    head = " ".join(body.split("\n0. ", 1)[1].split("\n1. ", 1)[0].split())
    assert "retry" in head and "before any work" in head
    assert "selfheal_fixed" in head and "log.jsonl" in head and "selfheal_lane.json" in head
    assert FP in head
    assert "never re-record" in head
    if remote:
        assert "hub-admin 'grep selfheal_fixed" in head

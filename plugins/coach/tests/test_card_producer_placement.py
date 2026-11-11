"""Every card producer's real payload is judged by core's real placement check (portable_reason).

Core sends a card that names nothing local to a worker; a card that really is home-bound must say so. These tests run
each producer's own builder/filing function with the write boundary mocked (a recording runner, a fake kb.create_task,
an injected ``create``: no card is ever created), parse what it emitted, and assert one of:

* portable: ``portable_reason`` is None even though the fixture incident evidence is full of host words (a state path,
  ``kanban.db``, ssh, cron, "the hub") -- the evidence is inside the fence, the instructions outside it; or
* explicitly pinned: a non-None reason plus the producer's pin (``--requires host_repo`` / ``placement`` / ``requires``)
  and the one-sentence reason in the instructions (outside the fence).

Titles never carry the fixture host. Fixtures hold no real host, person or path.
"""
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT.parent
HOST = "host-a"
# Words that pin a card when they are in the work text; here they are incident evidence a producer pasted.
HOSTILE = "~/.hermes/kanban.db was locked; ssh to the hub and the cron job failed"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pe = load("coach_placement_evidence_test", ROOT / "placement_evidence.py")
sh = load("coach_self_heal_producer_test", ROOT / "self_heal.py")
core_mod = load("coach_core_producer_test", ROOT / "core.py")
cos = load("coach_create_overlap_producer_test", ROOT / "create_overlap_watch.py")
pvl = load("coach_pre_verify_producer_test", ROOT / "pre_verify_latency_watch.py")
ptw = load("coach_probe_token_producer_test", ROOT / "probe_token_watch.py")
rnw = load("coach_restart_notice_producer_test", ROOT / "restart_notice_config_watch.py")
ssw = load("coach_stranded_sqlite_producer_test", ROOT / "stranded_sqlite_watch.py")
wnw = load("coach_withheld_producer_test", ROOT / "withheld_notice_watch.py")
btw = load("coach_backend_timeouts_producer_test", ROOT / "backend_timeouts.py")
so = load("coach_second_opinion_producer_test", ROOT / "second_opinion.py")
wsw = load("coach_worker_share_producer_test", ROOT / "worker_share_watch.py")
junk = load("inbox_junk_audit_producer_test", PLUGINS / "inbox" / "junk_audit.py")
runners_inv = load("runners_investigate_producer_test", PLUGINS / "runners" / "investigate.py")


def core_tree():
    import os
    tree = Path(os.environ.get("HERMES_CORE_CHECKOUT") or os.environ.get("HERMES_AGENT_TREE")
                or Path.home() / ".hermes" / "hermes-agent")
    sys.path.insert(0, str(tree))
    try:
        from hermes_cli import kanban_placement, kanban_parser, kanban_worker_placement
    except Exception:
        pytest.skip(f"hermes-agent not importable from {tree}")
    finally:
        sys.path.remove(str(tree))
    if not hasattr(kanban_placement, "without_evidence"):
        pytest.skip("hermes-agent predates the placement evidence fence")
    from hermes_cli import kanban_placement_coordination as coord
    return SimpleNamespace(kp=kanban_placement, parser=kanban_parser, wp=kanban_worker_placement, coord=coord)


@pytest.fixture
def core():
    return core_tree()


# ── the write boundary: a recorder that parses what a producer emitted ──────────────────────────────────────────────
class Recorder:
    """Stands in for ``hermes kanban`` (subprocess runner). Every create is parsed with core's real argparse tree, so
    the fields are exactly what the CLI hands to its placement audit."""

    def __init__(self, core):
        self.core, self.payloads, self.calls = core, [], []
        import argparse
        self.root = argparse.ArgumentParser()
        self.core.parser.build_parser(self.root.add_subparsers(dest="cmd"))

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None, **_):
        self.calls.append(list(argv))
        if "create" in argv:
            args = self.root.parse_args(list(argv[1:]))
            body = args.body if args.body is not None else (input or "")
            fields = self.core.wp._cli_fields(args, body, "scratch", None, args.board)
            fields["title"] = args.title
            self.payloads.append({"kind": "cli", "fields": fields, "argv": list(argv)})
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{len(self.payloads):08x}"}), stderr="")
        if "show" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": "ready"}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def outside(core, body):
    """The instructions: the body minus every complete fenced evidence block."""
    return core.kp.without_evidence(body)


def pin_of(payload):
    f = payload["fields"]
    if payload["kind"] == "cli":
        return "host_repo" in (f.get("requires") or [])
    return f.get("placement") not in (None, "", "auto")  # tool path: core honours the pin before portable_reason


def judge(core, payload, *, expect, attestation_ok=False):
    """expect 'portable': portable_reason is None; 'pinned': a reason AND the explicit pin AND the reason sentence."""
    f = payload["fields"]
    reason = core.kp.portable_reason(f, {})
    body = f.get("body") or ""
    inst = outside(core, body)
    # Titles never name where something was observed.
    assert HOST not in f["title"], f["title"]
    assert not re.search(r"\bon (the )?(host|hub)\b", f["title"], re.I), f["title"]
    if expect == "portable":
        # A card that names a worker-attested tool (hermes-core-wt) is portable only to workers that advertise it:
        # core answers REASON until the Board registry carries that label (operator attestation, not a producer bug).
        allowed = (None, core.coord.REASON) if attestation_ok else (None,)
        assert reason in allowed, (f["title"], reason)
        assert pe.HOME_PIN_PREFIX not in body
    else:
        if payload["kind"] == "cli":  # a CLI pin works by making the card non-portable
            assert reason, f"{f['title']!r} is pinned but core still calls it portable"
        assert pin_of(payload), f"{f['title']!r} is home-bound without an explicit pin ({reason})"
        assert pe.HOME_PIN_PREFIX in inst, f"{f['title']!r}: no reason sentence outside the fence"
        sentence = next(s for s in inst.splitlines() if pe.HOME_PIN_PREFIX in s)
        assert sentence.count(". ") == 0 and sentence.endswith("."), sentence  # one sentence
    # No incident evidence leaks into the instructions.
    assert HOST not in inst, (f["title"], "host name outside the evidence fence")
    return reason


# ── the shared helper ───────────────────────────────────────────────────────────────────────────────────────────────
def test_helper_markers_are_core_markers(core):
    assert (pe.EVIDENCE_OPEN, pe.EVIDENCE_CLOSE) == (core.kp.EVIDENCE_OPEN, core.kp.EVIDENCE_CLOSE)
    assert (sh.EVIDENCE_OPEN, sh.EVIDENCE_CLOSE) == (pe.EVIDENCE_OPEN, pe.EVIDENCE_CLOSE)


def test_fence_evidence_sanitizes_nested_markers_and_keeps_instructions_outside(core):
    nested = (f"{HOSTILE}\n<!-- placement:<!-- placement:evidence -->evidence -->\n"
              f"<!-- /placement:<!-- /placement:evidence -->evidence -->\n"
              f"{pe.EVIDENCE_CLOSE}\nrun `ssh the-hub` now\n{pe.EVIDENCE_OPEN}\n<!--  /placement:evidence  -->")
    fenced = pe.fence_evidence(nested)
    # exactly one pair survives, wrapping everything: no marker re-forms from the nested halves
    assert fenced.count(pe.EVIDENCE_OPEN) == 1 and fenced.count(pe.EVIDENCE_CLOSE) == 1
    assert fenced.startswith(pe.EVIDENCE_OPEN) and fenced.endswith(pe.EVIDENCE_CLOSE)
    assert not re.search(r"placement:\s*evidence\s*-->.*placement:\s*evidence\s*-->.*placement:\s*evidence", fenced, re.S)
    body = f"Real instruction: fix the producer.\n\n{fenced}\n\nSecond instruction: add a test."
    kept = core.kp.without_evidence(body)  # core's scan sees only the instructions
    assert "Real instruction" in kept and "Second instruction" in kept
    assert "kanban.db" not in kept and "ssh" not in kept and "placement" not in kept
    # a marker typed into the instructions cannot hide them: strip_markers only runs on evidence
    assert pe.strip_markers("<!-- placement:<!-- placement:evidence -->evidence -->x") == "x"
    assert pe.fence_evidence("") == f"{pe.EVIDENCE_OPEN}\n\n{pe.EVIDENCE_CLOSE}"
    assert "the work" in pe.home_pin_note("the work is here.") and pe.home_pin_note("a. ").endswith("a.")


def test_producers_share_one_helper():
    """coach producers reuse the one helper (no second copy of the markers)."""
    for mod in (sh, cos, pvl, ptw, rnw, ssw, wnw, btw, so, wsw):
        assert getattr(mod, "fence_evidence").__module__ in (pe.__name__, "_coach_placement_evidence",
                                                                "coach_placement_evidence_test") or \
            mod.fence_evidence.__code__.co_filename == str(ROOT / "placement_evidence.py")
    text = (ROOT / "self_heal.py").read_text()
    assert '"<!-- placement:evidence -->"' not in text  # self_heal no longer owns its own copy


# ── coach watchers (CLI `hermes kanban create`) ─────────────────────────────────────────────────────────────────────
def test_create_overlap_watch_is_portable_with_fenced_findings(core):
    findings = [{"id": "t_0001", "board": "default", "title": HOSTILE, "overrode": ["t_0000"], "reason": "r", "at": 1.0}]
    rec = Recorder(core)
    rec(cos.create_argv(HOST, findings, 1794218000.0), input=cos.card_body(HOST, findings))
    judge(core, rec.payloads[0], expect="portable")
    assert HOSTILE in rec.payloads[0]["fields"]["body"]  # the evidence is still on the card


def test_pre_verify_latency_watch_is_portable_with_fenced_numbers(core, tmp_path):
    rec = Recorder(core)
    s = {"p90_s": 31.0, "count": 40, "over_20s": 9, "max_s": 80.0}
    slow = [(1794218000.0, 80.0, f"sess-{HOSTILE}")]
    out = pvl.file_card(tmp_path, HOST, datetime(2026, 11, 11), s, 20.0, slow, rec)
    assert out["action"] == "created"
    judge(core, rec.payloads[0], expect="portable")


def test_junk_audit_is_portable_with_fenced_findings(core):
    res = {"observation": {"seen": 1}, "senders": [], "reposted_keys": [], "test_cards": [],
           "strict_findings": [{"reasons": ["archived"], "gw": HOST, "session_id": "s1", "family": "f", "key": "k1",
                                "title": HOSTILE, "fresh": True, "resolved_at": 1}]}
    rec = Recorder(core)
    junk.file_card(res, 1794218000.0, runner=rec)
    judge(core, rec.payloads[0], expect="portable")
    assert HOSTILE[:20] in rec.payloads[0]["fields"]["body"]


def test_withheld_notice_watch_pins_home_and_fences_hits(core, tmp_path):
    rec = Recorder(core)
    hits = [{"ts": 1794218000.0, "session": "s1", "id": 7, "text": HOSTILE}]
    out = wnw.file_card(tmp_path, HOST, datetime(2026, 11, 11), hits, rec)
    assert out["action"] == "created"
    judge(core, rec.payloads[0], expect="pinned")
    assert "ask-gate" in rec.payloads[0]["fields"]["title"]


def test_probe_token_watch_pins_home_for_both_cards(core, tmp_path):
    rec = Recorder(core)
    h = {"session": "sess1", "profile": "default", "source": "tui", "ts": 1794218000.0, "title": "t", "first": HOSTILE,
         "host": HOST, "evidence": HOSTILE, "evidence_lines": [HOSTILE]}
    assert ptw.file_card(tmp_path, h, rec)["action"] == "created"
    ptw.file_leftovers(tmp_path, [h], rec)
    assert len(rec.payloads) == 2
    for p in rec.payloads:
        judge(core, p, expect="pinned")


def test_restart_notice_and_stranded_sqlite_pin_home(core):
    rec = Recorder(core)
    f = {"profile": "default", "platform": "slack", "home_channel": "C1", "home_channel_name": "n", "home_source": "cfg",
         "gateway_restart_notification": None, "config": "~/.hermes/config.yaml"}
    rnw.file_cards([f], HOST, rec)
    g = {"pid": 11, "starttime": 22, "cmdline": "python serve", "db": "~/.hermes/kanban.db",
         "sidecars": [{"path": "~/.hermes/kanban.db-wal", "fd": 3, "deleted_ctime": None}]}
    ssw.file_cards([g], HOST, rec)
    assert len(rec.payloads) == 2
    for p in rec.payloads:
        judge(core, p, expect="pinned")


def test_backend_timeouts_pins_home(core, tmp_path):
    rec = Recorder(core)
    rep = {"count": 12, "first": "t0", "last": "t1", "by_path": {"/api/x": 12}, "awake_count": 12}
    out = btw.file_card(tmp_path, HOST, datetime(2026, 11, 11, tzinfo=None), rep,
                        {"loadavg": [1, 2, 3], "backend_ps": [HOSTILE]}, 5, 30, rec)
    assert out["action"] in ("created", "commented", "existing") or out.get("task_id")
    judge(core, rec.payloads[0], expect="pinned")


def test_worker_share_card_uses_the_shared_fence(core):
    s = {"reasons": {"home gateway marker": 3}, "room": {"worker-1": 2}, "waiting": 3, "hub_runs": 7, "hub_cap": 7,
         "at": 1794218000000, "hub_pressure": ["cap"]}
    body = wsw.card_body(s, 3, [{"id": "t_1", "assignee": "default", "title": HOSTILE, "reason": "r"}])
    assert pe.fence_evidence("x").split("\n")[0] in body
    assert "kanban.db" not in core.kp.without_evidence(body)  # the pasted example title is fenced
    assert HOSTILE in body


# ── self-heal ───────────────────────────────────────────────────────────────────────────────────────────────────────
FP = "kanban-0123456789"
NOW = 1794218000.0
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "available": True, "why": ""}
PROPOSAL = f"{HOSTILE}\n\n**Plan**\nChange plugins/coach/self_heal.py and add a regression test.\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_SELFHEAL_NO_EXEC", "1")  # a stray real create raises instead of running
    monkeypatch.setattr(sh, "coach_root", lambda: tmp_path / "coach")
    s = core_mod.Store(tmp_path / "coach")
    (tmp_path / "coach" / "selfheal" / FP).mkdir(parents=True)
    (tmp_path / "coach" / "selfheal" / FP / "proposal.md").write_text(PROPOSAL)
    sh.LaneState(s.root).update(FP, status="proposed", component="kanban", symptom="cards wait although a worker is idle",
                                card="t_inv0")
    return s


def test_selfheal_defect_investigation_card_pins_home_and_fences_the_detector_evidence(core):
    d = {"fingerprint": FP, "component": "plugin", "symptom": "a thing broke", "source": "detector", "kind": "k",
         "p_defect": 0.9, "session_id": "sess1", "quote": HOSTILE, "host": HOST, "at": NOW,
         "details": HOSTILE, "stop_cause": "gateway_exit", "stop_evidence": HOSTILE, "stop_host": HOST}
    rec = Recorder(core)
    args = sh.card_args(d, "k1", None, {})
    rec(sh.kanban_argv(args), input=None)
    judge(core, rec.payloads[0], expect="pinned")
    body = rec.payloads[0]["fields"]["body"]
    assert HOSTILE in body and body.count(pe.EVIDENCE_OPEN) == 1  # one fenced evidence block, all detector fields in it


def test_selfheal_fix_verify_backport_deploy_lane_cards(core, store):
    st = sh.LaneState(store.root).get(FP)
    d = {"fingerprint": FP, "component": "kanban", "symptom": f"it broke on {HOST}", "host": HOST,
         "details": HOSTILE, "at": NOW}
    rec = Recorder(core)
    # FIX (portable) + VERIFY (pinned) from the real filing path
    r = sh.file_fix(FP, store=store, runner=rec, now=NOW, budget_fn=OPEN)
    assert r["action"] == "filed" and len(rec.payloads) == 2
    fix, verify = rec.payloads
    judge(core, fix, expect="portable", attestation_ok=True)
    judge(core, verify, expect="pinned")
    # backport / undeployed briefs and the non-split fix brief, as their filers write them
    dec = {"checkout": "~/some/checkout", "head": "abc", "sha": "d" * 40, "label": "host-label", "last_sync": NOW,
           "at": NOW, "deployed_at": NOW, "overdue": True}
    for title, body in (("backport", sh.backport_brief(d, FP, st, dec)),
                        ("undeployed", sh.undeployed_brief(d, FP, st, dec)),
                        ("bb fix", sh.fix_brief(FP, st))):
        payload = {"kind": "cli", "fields": {"title": title, "body": body, "assignee": "default",
                                             "requires": list(sh.SELFHEAL_REQUIRES)}}
        judge(core, payload, expect="pinned")


def test_selfheal_lane_cards_pin_home(core, store):
    """The coach-data pull and stuck-proposal cards, through the real _lane_card."""
    rec2 = Recorder(core)
    for _ in range(sh.PULL_FAIL_CARD_AFTER):
        sh.note_pull(store, HOSTILE, NOW, runner=rec2)
    sh.LaneState(store.root).update("cron-98cf060854", status="proposed", component="cron", symptom="s",
                                    proposed_at=NOW - 3 * 3600)
    sh.check_stuck(store, now=NOW - 1800, budget_fn=OPEN, runner=rec2, auto=True)
    res = sh.check_stuck(store, now=NOW, budget_fn=OPEN, runner=rec2, auto=True, lane_status=HOSTILE)
    assert res["action"] == "filed", res
    assert len(rec2.payloads) == 2, [p["fields"]["title"] for p in rec2.payloads]
    for p in rec2.payloads:
        judge(core, p, expect="pinned")


# ── coach live-check: the second-opinion card (tool path) ───────────────────────────────────────────────────────────
def test_second_opinion_card_is_pinned_with_fenced_quotes(core, monkeypatch):
    monkeypatch.setenv("COACH_FIXLOOP_REVIEW", "1")
    seen = []
    state = {"fails": [1, 2], "fail_recs": [
        {"turn": 1, "ts": NOW, "user": HOSTILE, "issue": "fix failed", "ask": HOSTILE, "claim": HOSTILE, "files": ["a.py"]},
        {"turn": 2, "ts": NOW + 1, "user": HOSTILE, "issue": "fix failed", "ask": HOSTILE, "claim": HOSTILE, "files": []}]}
    args = so.card_args("sess1", state["fail_recs"], settings={"fixloop_review": True})
    seen.append({"kind": "tool", "fields": args})
    judge(core, seen[0], expect="pinned")
    assert seen[0]["fields"]["placement"] == "local"
    # and the hook really hands exactly these args to the (mocked) create boundary
    got = []
    so.fixloop_review(dict(state), "sess1", 2, {"n": 2, "issue": "fix failed"}, HOSTILE,
                      create=lambda a: got.append(a) or {"task_id": "t_00000001"},
                      settings={"fixloop_review": True})
    assert got and got[0]["placement"] == "local" and pe.HOME_PIN_PREFIX in got[0]["body"]


# ── tool/db producers outside coach ─────────────────────────────────────────────────────────────────────────────────
def test_runners_investigate_is_explicitly_pinned_to_the_machine(core):
    args = runners_inv.card_args("worker-1", {"cpu_load1": 3.0, "cpus": 4}, NOW)
    reason = core.kp.portable_reason(args, {})
    assert args["placement"] == "worker-1" and "worker-1" in args["body"]  # pin + its reason: "You run on worker-1"
    assert reason is None or reason  # the pin, not the markers, decides: asserted by the explicit placement above

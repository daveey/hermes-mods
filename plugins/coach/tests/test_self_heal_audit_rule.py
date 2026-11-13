"""self-heal turn-end audit: findings and their false alarms carry the audit rule they were judged under.

t_01a1d3759039b97a ("6 admission findings closed not_defect in 7 days"): 5 of the 6 rows had already been analysed by
two earlier detector cards (t_01a1cfebde1c8c0a, t_01a1d0545bcd7bc3) and 3 fixed by a third (5720fd3e, the
scratch_iteration kind). The 6th, agent-b07b407fde, was detected 2026-11-11 18:45 under the pre-5720fd3e rule, waited
two days in the deferred queue on the lane's spend cap and concurrency, was filed, and closed not_defect. The
current rule keeps all 7 gate negatives (that row included) below the filing threshold on 3/3 samples. Two gaps: the
negative counter counted
rows from rules a detector card had already changed, and a detection deferred across a rule change filed without
being judged by the new rule.
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


sh = load("coach_self_heal_audit_rule_test", ROOT / "self_heal.py")
core = load("coach_core_test_audit_rule", ROOT / "core.py")

NOW = 1794549000.0
DAY = 86400
RULE = getattr(sh, "AUDIT_RULE", "current-rule")  # getattr/raising=False: a RED run on old code reaches the asserts
OLD = "0ld0ld0ld0ld"


class Runner:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if "show" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": "ready"}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


class Board:
    """Fake engine_budget with room for one more investigation; `create` adds a ready card."""

    def __init__(self):
        self.cards, self.n = [], 0

    def eb(self):
        return SimpleNamespace(
            snapshot=lambda **kw: {"lanes": {"selfheal": 1.0}, "caps": {"selfheal": 200.0}},
            check=lambda snap, lane, extra=0.0: (True, ""),
            engine_cards=lambda home, b, st: [c for c in self.cards if c["status"] in st],
            card_budget=lambda c, snap: 8.0, card_cost=lambda home, b, tid: 8.0)

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        if "create" in argv:
            self.n += 1
            tid = f"t_{self.n:08x}"
            self.cards.append({"id": tid, "lane": "selfheal", "status": "ready", "title": argv[argv.index("create") + 1]})
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_SELFHEAL_AUTO_APPROVE", "1")
    monkeypatch.setenv("COACH_DATA", str(tmp_path / "coach-data"))
    (tmp_path / "home").mkdir()
    return core.Store(tmp_path / "coach")


def events(store, name):
    p = Path(store.root) / "log.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return [r for r in rows if r.get("event") == name]


def negatives(store):
    p = Path(store.root).parent / "coach-data" / "eval" / "admission-negatives.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def not_defect(store, fp, rule, at, runner):
    sh.LaneState(store.root).update(fp, status="filed", card="t_aaaaaaaa", component="agent",
                                    symptom=f"symptom {fp}", session_id=f"sess-{fp}", filed_at=at)
    store.log({"event": "selfheal_detect", "fingerprint": fp, "source": "admission", "p": 0.95,
               "session_id": f"sess-{fp}", "symptom": f"symptom {fp}", "evidence": "self-caught, fixed in-turn",
               **({"audit_rule": rule} if rule else {})})
    sh.record_event("not_defect", fp, "t_aaaaaaaa", note="self-caught", store=store, now=at, runner=runner)


def test_the_rule_changes_with_the_prompt_and_every_threshold(monkeypatch):
    base = sh._audit_rule()
    assert base == sh.AUDIT_RULE and len(base) == 12
    monkeypatch.setattr(sh, "AUDIT_SYS", sh.AUDIT_SYS + "\nkind foo (not a defect): ...")
    assert sh._audit_rule() != base
    monkeypatch.undo()
    monkeypatch.setattr(sh, "CONFIRM_THRESHOLDS", {**sh.CONFIRM_THRESHOLDS, "claude-sonnet-5-5": 0.85})
    assert sh._audit_rule() != base
    monkeypatch.undo()
    monkeypatch.setattr(sh, "AUDIT_THRESHOLD", 0.95)
    assert sh._audit_rule() != base


def test_an_admission_carries_its_rule_into_the_log_and_its_saved_turn(store):
    def judge(system, prompt):
        return json.dumps({"p_defect": 0.95, "kind": "admission", "component": "agent", "symptom": "wrong claim"})
    hit = sh.detect_admission("I was wrong: the PR was merged.", llm=judge, tool_log=["   <- ERROR x"], ask="status?")
    assert hit["audit_rule"] == RULE
    rec = sh.handle(hit, "sess-1", store, evidence="I was wrong", m="shadow")
    assert rec["audit_rule"] == RULE
    saved = json.loads((Path(store.root) / "selfheal" / hit["fingerprint"] / sh.AUDIT_INPUT_FILE).read_text())
    assert saved["audit_rule"] == RULE


def test_false_alarms_of_an_older_rule_open_no_detector_card(store):
    """The t_01a1d3759039b97a replay: five not_defects from older rules (two before rows carried a rule) plus one
    from the current rule are one current false alarm, not six. Three under the current rule still open the card,
    which lists only those."""
    r = Runner()
    for i, (fp, rule) in enumerate((("agent-c9e7ffb868", ""), ("agent-110a933b6c", ""), ("agent-ad20c8a5f7", OLD),
                                    ("plugin-1111111111", OLD), ("kanban-44675cd4ce", OLD))):
        not_defect(store, fp, rule, NOW - DAY + i * 600, r)
    not_defect(store, "agent-b07b407fde", RULE, NOW, r)
    assert len(negatives(store)) == 6 and not r.creates()
    last = events(store, "selfheal_admission_negative")[-1]
    assert (last["recent"], last["older_rule"]) == (1, 5)
    not_defect(store, "agent-2222222222", RULE, NOW + 600, r)
    not_defect(store, "agent-3333333333", RULE, NOW + 1200, r)
    assert len(r.creates()) == 1
    body = r.creates()[0][r.creates()[0].index("--body") + 1] if "--body" in r.creates()[0] else " ".join(r.creates()[0])
    assert "agent-b07b407fde" in body and "agent-3333333333" in body and "agent-ad20c8a5f7" not in body


def deferred_admission(store, rule, text="Issue corrected before delivery: first draft read the link with lstat."):
    d = {"component": "agent", "symptom": "collector misread dangling link", "source": "admission",
         "kind": "admission", "p_defect": 0.97, "session_id": "20261111_183208_dd31c5", "quote": text[-300:],
         "host": "hermes-hub", "platform": "kanban", "at": NOW - 2 * DAY}
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    p = Path(store.root) / "selfheal" / d["fingerprint"] / sh.AUDIT_INPUT_FILE
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"text": text, "ask": "build the collector", "tool_log": ["-> terminal(pytest)"],
                             **({"audit_rule": rule} if rule else {})}))
    return d  # the drained queue row: no audit_input, no audit_rule (drain() keeps 5 keys)


def test_a_detection_deferred_across_a_rule_change_is_rejudged_and_withdrawn(store, monkeypatch):
    board = Board()
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    seen = []

    def rejudge(inp):
        seen.append(inp["text"])
        return {"p_defect": 0.0, "kind": "scratch_iteration", "component": "agent", "symptom": "x",
                "stages": "judge"}
    monkeypatch.setattr(sh, "_rejudge_admission", rejudge, raising=False)
    d = deferred_admission(store, OLD)
    out = sh.file_defect(d, store=store, m="live", runner=board, now=NOW, cfg={})
    assert out == {"action": "skip", "why": "audit_rule_changed"} and not board.cards
    assert seen == [json.loads((Path(store.root) / "selfheal" / d["fingerprint"] / sh.AUDIT_INPUT_FILE)
                               .read_text())["text"]]
    w = events(store, "selfheal_withdrawn")[-1]
    assert (w["why"], w["old_rule"], w["rule"]) == ("audit_rule_changed", OLD, RULE)
    assert events(store, "selfheal_rejudged")[-1]["files"] is False


def test_a_rejudge_that_still_hits_files_and_restamps_the_rule(store, monkeypatch):
    board = Board()
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    monkeypatch.setattr(sh, "_rejudge_admission", lambda inp: {
        "p_defect": 0.96, "kind": "admission", "component": "agent", "symptom": "posted a wrong claim",
        "stages": "judge+confirm", "confirm_model": "gpt-6.1-sol"}, raising=False)
    d = deferred_admission(store, None)  # saved before turns carried a rule: unknown = stale
    out = sh.file_defect(d, store=store, m="live", runner=board, now=NOW, cfg={})
    assert out["action"] == "filed" and len(board.cards) == 1
    saved = json.loads((Path(store.root) / "selfheal" / d["fingerprint"] / sh.AUDIT_INPUT_FILE).read_text())
    assert saved["audit_rule"] == RULE  # a later not_defect on it counts against the current rule


@pytest.mark.parametrize("case", ["same_rule", "no_saved_turn", "judge_error", "no_verdict"])
def test_no_rejudge_or_a_failed_one_files_as_before(store, monkeypatch, case):
    board = Board()
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    calls = []

    def rejudge(inp):
        calls.append(1)
        if case == "judge_error":
            raise RuntimeError("provider down")
        return None
    monkeypatch.setattr(sh, "_rejudge_admission", rejudge, raising=False)
    d = deferred_admission(store, RULE if case == "same_rule" else OLD)
    if case == "no_saved_turn":
        (Path(store.root) / "selfheal" / d["fingerprint"] / sh.AUDIT_INPUT_FILE).unlink()
    out = sh.file_defect(d, store=store, m="live", runner=board, now=NOW, cfg={})
    assert out["action"] == "filed" and len(board.cards) == 1
    assert bool(calls) == (case in ("judge_error", "no_verdict"))
    if calls:
        assert events(store, "selfheal_rejudge_error")


def test_other_sources_are_never_rejudged(store, monkeypatch):
    board = Board()
    monkeypatch.setattr(sh, "_budget_mod", board.eb)
    monkeypatch.setattr(sh, "_rejudge_admission", lambda inp: pytest.fail("complaints are not audit findings"),
                        raising=False)
    d = deferred_admission(store, OLD)
    d["source"] = "complaint"
    assert sh.file_defect(d, store=store, m="live", runner=board, now=NOW, cfg={})["action"] == "filed"

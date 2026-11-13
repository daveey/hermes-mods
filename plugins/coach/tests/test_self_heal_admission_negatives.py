"""self-heal detection guard for audit false alarms: a `not_defect` verdict on a turn-end audit (admission) finding is
a negative replay row, and a repeat inside a week opens one detector card (agent-feed37636b: three honest, allowed
scope notes closed not_defect in one day and nothing counted them)."""
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


sh = load("coach_self_heal_adm_neg_test", ROOT / "self_heal.py")
core = load("coach_core_test_adm_neg", ROOT / "core.py")

NOW = 1794290000.0
DAY = 86400


class Runner:
    """Fake subprocess.run: `kanban create` returns a new id, `kanban show` reports created cards as `ready`
    (or `done` once closed), `kanban comment` succeeds."""

    def __init__(self):
        self.calls, self.n, self.done = [], 0, set()

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if "show" in argv:
            card = argv[argv.index("show") + 1]
            st = "done" if card in self.done else "ready"
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": st}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]

    def comments(self):
        return [a for a in self.calls if "comment" in a]


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = core.Store(tmp_path / "coach")
    data = tmp_path / "coach-data"
    monkeypatch.setenv("COACH_DATA", str(data))
    return store, data


def seed(store, fp, source="admission", at=NOW, rule=None):
    """A fingerprint the lane filed from a detector `source` (as file_defect logs it), judged under `rule`
    (default: the current AUDIT_RULE)."""
    sh.LaneState(store.root).update(fp, status="filed", card="t_aaaaaaaa", component="agent",
                                    symptom=f"symptom {fp}", session_id=f"sess-{fp}", filed_at=at)
    store.log({"event": "selfheal_detect", "fingerprint": fp, "source": source, "p": 0.95,
               "session_id": f"sess-{fp}", "symptom": f"symptom {fp}", "evidence": f"Not reviewed: X ({fp})",
               **({"audit_rule": rule or sh.AUDIT_RULE} if rule != "" else {})})


def negatives(data):
    p = data / "eval" / "admission-negatives.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_three_admission_not_defects_in_a_week_open_exactly_one_card(env):
    store, data = env
    r = Runner()
    for i, fp in enumerate(("agent-0000000001", "agent-0000000002", "agent-0000000003")):
        seed(store, fp)
        sh.record_event("not_defect", fp, "t_aaaaaaaa", note="allowed scope note", store=store,
                        now=NOW + i * 3600, runner=r)
    rows = negatives(data)
    assert [x["id"] for x in rows] == ["agent-0000000001", "agent-0000000002", "agent-0000000003"]
    assert all(x["label"] == 0 and x["source"] == "admission" and x["session_id"].startswith("sess-") for x in rows)
    assert len(r.creates()) == 1 and not r.comments()
    argv = r.creates()[0]
    title = argv[argv.index("create") + 1]
    assert "audit" in title.lower() and "not_defect" in title
    # a 4th case while that card is open comments on it, never a second card
    seed(store, "agent-0000000004")
    sh.record_event("not_defect", "agent-0000000004", "t_aaaaaaaa", note="x", store=store, now=NOW + 4 * 3600,
                    runner=r)
    assert len(r.creates()) == 1 and len(r.comments()) == 1 and "t_00000001" in r.comments()[0]
    # once that card is done, the next repeat opens a fresh one
    r.done.add("t_00000001")
    seed(store, "agent-0000000005")
    sh.record_event("not_defect", "agent-0000000005", "t_aaaaaaaa", note="x", store=store, now=NOW + 5 * 3600,
                    runner=r)
    assert len(r.creates()) == 2
    assert [e for e in (json.loads(x) for x in (store.root / "log.jsonl").read_text().splitlines())
            if e.get("event") == "selfheal_admission_negative"]


def test_two_cases_or_old_cases_open_no_card(env):
    store, data = env
    r = Runner()
    for i, fp in enumerate(("agent-000000000a", "agent-000000000b")):
        seed(store, fp)
        sh.record_event("not_defect", fp, "t_aaaaaaaa", store=store, now=NOW - 10 * DAY + i, runner=r)
    seed(store, "agent-000000000c")
    sh.record_event("not_defect", "agent-000000000c", "t_aaaaaaaa", store=store, now=NOW, runner=r)
    assert len(negatives(data)) == 3 and not r.creates()  # only 1 inside the 7-day window


def test_non_admission_not_defect_appends_nothing(env):
    store, data = env
    r = Runner()
    for i in range(3):
        fp = f"desktop-00000000{i}{i}"
        seed(store, fp, source="complaint")
        sh.record_event("not_defect", fp, "t_aaaaaaaa", store=store, now=NOW + i, runner=r)
    assert negatives(data) == [] and not r.creates()


def test_a_not_defect_admission_becomes_a_replayable_row(env, tmp_path):
    """The audited turn (reply, request, tool log) travels with the filed admission and lands in its negative row, so
    `selfheal_replay.py --mode admission` runs on the file as written. Before, the row held only a 300-char reply
    tail and every detector card rebuilt each turn by hand from the detecting host's state.db (plugin-aafb20159a)."""
    store, data = env
    reply = "Fixed a false reminder: the plugin now ignores late subscriptions. I was wrong earlier about X."
    log = ["-> terminal(pytest plugins/follow-through)", "   <- ERROR 2 failed"]

    def judge(system, prompt):
        return json.dumps({"p_defect": 0.95, "kind": "admission", "component": "plugin", "symptom": "false reminder"})
    hit = sh.detect_admission(reply, llm=judge, tool_log=log, ask="why did I get this reminder?")
    assert hit and hit["audit_input"] == {"text": reply, "ask": "why did I get this reminder?", "tool_log": log}
    fp = hit["fingerprint"]
    d = {**hit, "session_id": "sess-x", "host": "macbook", "quote": reply[-300:], "at": NOW}
    sh.file_defect(d, store=store, m="shadow", cfg={}, now=NOW)
    assert json.loads((store.root / "selfheal" / fp / sh.AUDIT_INPUT_FILE).read_text())["text"] == reply
    seed(store, fp)
    sh.record_event("not_defect", fp, "t_aaaaaaaa", note="already fixed in-turn", store=store, now=NOW,
                    runner=Runner())
    row = negatives(data)[-1]
    assert (row["id"], row["text"], row["ask"], row["tool_log"]) == (fp, reply, "why did I get this reminder?", log)
    # the replay judges it as is, and a legacy row with no audited turn is listed, not a crash
    path = data / sh.ADMISSION_NEG_FILE
    path.write_text(path.read_text() + json.dumps({"id": "agent-legacy0000", "label": 0, "evidence": "tail"}) + "\n")
    replay = load("selfheal_replay_negatives_test", ROOT / "eval" / "selfheal_replay.py")
    out = tmp_path / "out"
    replay.main(["--data", str(path), "--out", str(out), "--mode", "admission", "--workers", "1"],
                llm=lambda s, p: json.dumps({"p_defect": 0.1, "kind": "none", "component": "agent", "symptom": ""}))
    rep = json.loads((out / "report.json").read_text())
    assert list(rep["per_row"]) == [fp] and rep["per_row"][fp]["ps"] == [0.1]


def test_machine_signals_and_misses_carry_no_audit_input():
    assert sh.detect_admission("All done.", llm=lambda s, p: "{}") is None
    sig = sh.detect_admission("", tool_log=["-> terminal(x)", "^C interrupted"]) or {}
    assert "audit_input" not in sig


def test_a_fix_that_predates_the_filing_is_an_adoption_miss_not_an_audit_false_alarm(env, tmp_path, monkeypatch):
    """plugin-aafb20159a: the audit flagged a real plugin bug the agent had already fixed (2c0772cc, 8 min before the
    admission). The investigation closed it not_defect citing that commit; counting it as an audit false alarm opened
    a detector card whose audit had been right. A not_defect whose cited fix predates the filing is logged as
    selfheal_late_adopt and is no negative row."""
    import os
    import subprocess
    store, data = env
    repo = tmp_path / "repo"
    repo.mkdir()
    git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_COMMITTER_DATE": f"{int(NOW) - 600} +0000",
               "GIT_AUTHOR_DATE": f"{int(NOW) - 600} +0000"}
    for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "fix: late subscriptions"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, env=git_env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                         check=True).stdout.strip()
    monkeypatch.setattr(sh, "_fix_repos", lambda repo_=None: [repo])
    r = Runner()
    for i, fp in enumerate(("agent-00000000a1", "agent-00000000a2")):
        seed(store, fp)
        sh.record_event("not_defect", fp, "t_aaaaaaaa", note="scratch slip", store=store, now=NOW + i, runner=r)
    seed(store, "plugin-00000000a3")
    sh.record_event("not_defect", "plugin-00000000a3", "t_aaaaaaaa", note=f"already fixed by {sha[:12]}",
                    store=store, now=NOW + 2, runner=r)
    assert [x["id"] for x in negatives(data)] == ["agent-00000000a1", "agent-00000000a2"] and not r.creates()
    log = [json.loads(x) for x in (store.root / "log.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "selfheal_late_adopt" and e.get("fingerprint") == "plugin-00000000a3" for e in log)
    assert {"event": "selfheal_admission_negative", "fingerprint": "plugin-00000000a3", "action": "skip",
            "why": "already_fixed", "commit": sha} in [{k: v for k, v in e.items() if k != "ts"} for e in log]
    # a not_defect citing a commit made AFTER the filing is still an audit false alarm (3rd in the week: one card)
    seed(store, "agent-00000000a4", at=NOW - 3600 * 24)
    sh.LaneState(store.root).update("agent-00000000a4", filed_at=NOW - 3600)
    git_env.update(GIT_COMMITTER_DATE=f"{int(NOW)} +0000", GIT_AUTHOR_DATE=f"{int(NOW)} +0000")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "later"], check=True, env=git_env,
                   capture_output=True)
    later = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                           check=True).stdout.strip()
    sh.LaneState(store.root).update("agent-00000000a4", filed_at=NOW - 3600)
    sh.record_event("not_defect", "agent-00000000a4", "t_aaaaaaaa", note=f"see {later[:12]}", store=store,
                    now=NOW + 3, runner=r)
    assert len(negatives(data)) == 3 and len(r.creates()) == 1

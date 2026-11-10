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


def seed(store, fp, source="admission", at=NOW):
    """A fingerprint the lane filed from a detector `source` (as file_defect logs it)."""
    sh.LaneState(store.root).update(fp, status="filed", card="t_aaaaaaaa", component="agent",
                                    symptom=f"symptom {fp}", session_id=f"sess-{fp}", filed_at=at)
    store.log({"event": "selfheal_detect", "fingerprint": fp, "source": source, "p": 0.95,
               "session_id": f"sess-{fp}", "symptom": f"symptom {fp}", "evidence": f"Not reviewed: X ({fp})"})


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

"""plugin-488520f036: an admission's symptom fused two clauses, "merge guard fails open on wrapped commands; skipped
skill and checklist step admitted". The session's own fix card, "ship-only-guard fails open behind exec-style
wrappers: ... + `gh pr merge` pass unchecked", answered the first clause, but the whole-symptom rule needed 4 of 10
distinctive words in its title and found 3 (fail, guard, merg): the meta clause added 5 words no fix card carries, and
"wrappers" stemmed to "wrapper" while "wrapped" stemmed to "wrapp". Adoption refused the owner as "no subject
evidence", the commit check missed a99f3c49 the same way, and the lane filed an investigation card three hours after
the fix had shipped. A compound symptom is now also judged clause by clause (a stricter half-the-words rule), and a
doubled-consonant agent noun (wrapper/runner) shares its verb's stem."""
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


sh = load("coach_self_heal_compound_symptom_test", ROOT / "self_heal.py")
core = load("coach_core_compound_symptom_test", ROOT / "core.py")

NOW = 1794696100.0
SID = "20261112_052539_8d8534"
SYMPTOM = "merge guard fails open on wrapped commands; skipped skill and checklist step admitted"
OWNER = {"id": "t_01a1dc2a6e808b24", "status": "done", "board": None, "created_at": NOW - 900,
         "title": "ship-only-guard fails open behind exec-style wrappers: `token-broker exec … --`, `timeout`, "
                  "`nice`, `stdbuf` + `gh pr merge` pass unchecked",
         "body": "The guard only unwraps the wrappers it knows."}
UNRELATED = {"id": "t_unrelated01", "status": "running", "board": None, "created_at": NOW - 600,
             "title": "Close #3076's card after its post-deploy check", "body": "Skill checklist follow-up."}


def defect(symptom=SYMPTOM):
    d = {"component": "plugin", "symptom": symptom, "source": "admission", "kind": "admission", "p_defect": 0.88,
         "session_id": SID, "quote": "the guard only unwraps the wrappers it knows, so it fails open",
         "host": "hermes-hub", "at": NOW - 60, "complaint_at": NOW - 60}
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


def test_fingerprint_is_unchanged():
    # _stem feeds the fingerprint: the subject stemmer must not move any lane's dedupe key.
    assert defect()["fingerprint"] == "plugin-488520f036"


def test_owner_card_answering_one_clause_names_the_compound_defect():
    assert sh.card_names_defect(OWNER, defect())


def test_fix_commit_subject_binds_the_compound_defect():
    commit = {"title": "fix(ship-only-guard): inspect protected commands behind exec wrappers", "body": ""}
    assert sh.card_names_defect(commit, defect())


def test_agent_noun_shares_its_verb_stem():
    assert sh._subject_words("wrappers") == sh._subject_words("wrapped") == {"wrapp"}
    assert sh._subject_words("runners") == sh._subject_words("running")


@pytest.mark.parametrize("title", [
    "Close #3076's card after its post-deploy check",
    "merge #3102 once the state turn expires",          # one clause word (merg) is never enough
    "guard skill loading",                              # one word from each clause does not add up
])
def test_clause_rule_still_needs_most_of_one_clause(title):
    assert not sh.card_names_defect({"title": title, "body": ""}, defect())


def test_single_clause_symptom_keeps_the_whole_symptom_rule():
    d = defect("archived session remains visible in sidebar")
    assert sh.card_names_defect({"title": "Sidebar keeps showing archived sessions", "body": ""}, d)
    assert not sh.card_names_defect({"title": "Sidebar width", "body": ""}, d)


class Runner:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append(list(argv))
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def OPEN(board=None, now=None):
    return {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True, "why": ""}


def test_lane_adopts_the_shipped_owner_instead_of_filing(tmp_path):
    store = core.Store(tmp_path / "coach")
    r = Runner()
    out = sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW, cfg={}, budget_fn=OPEN,
                         owner_fn=lambda sid, since: [dict(UNRELATED), dict(OWNER)])
    assert out["action"] == "skip" and out["card"] == OWNER["id"], out
    assert not r.creates()

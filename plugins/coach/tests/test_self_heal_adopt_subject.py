"""agent-e5b6dcf428: a coach report_only signal from one session adopted that session's next done card (an unrelated
Kanban owner-death fix) as its owner, because the relevance check passed on any one shared word ("agent", "check").
Session adoption of a defect that is not a user complaint in that session now needs subject evidence: the
fingerprint, the symptom text, the signal's non-generic tool, or several of its distinctive words."""
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


sh = load("coach_self_heal_adopt_subject_test", ROOT / "self_heal.py")
core = load("coach_core_adopt_subject_test", ROOT / "core.py")

NOW = 1792660000.0
HUB = {}  # no hub configured = this host files locally (the hub itself)
SID = "20261026_235722_bef6c6"
SYMPTOM = ("agent's revised reply still failed the coach report_only check: What's missing: a daytime source that "
           "knows which room you're in.")
# An unrelated card the same session created after the signal: generic words only (agent, check, which, what,
# coach, missing), the overlap that let the incident adopt the owner-death fix.
UNRELATED = {"id": "t_0wnerdeath1", "status": "done", "board": None,
             "title": "One unclean gateway death parks every in-flight Kanban card in sticky triage",
             "body": "Checked: the agent's runs and what the dispatcher did. No other check covers which cards were "
                     "missing an owner. The coach still holds a copy."}


def OPEN(board=None, now=None):
    return {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True, "why": ""}


class Runner:
    """Fake subprocess.run: records argv; `hermes kanban ... create` returns a new task id."""

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


@pytest.fixture
def store(tmp_path):
    return core.Store(tmp_path / "coach")


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def signal_defect(source="signal", symptom=SYMPTOM, **kw):
    d = {"component": "agent", "symptom": symptom, "source": source, "kind": "coach", "p_defect": 0.9,
         "session_id": SID, "quote": symptom, "host": "macbook", "at": NOW - 60,
         "details": "coach reply_check report_only nudge (p=0.9): What's missing: a daytime source"}
    d.update(kw)
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


def owner(cards):
    return lambda sid, since: sorted(({"created_at": NOW - 30, **c} for c in cards), key=lambda c: -c["created_at"])


def file(store, d, cards):
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN, owner_fn=owner(cards))
    return out, r


@pytest.mark.parametrize("source", ["signal", "backstop"])
def test_unrelated_done_card_from_the_same_session_is_not_adopted(store, source):
    out, r = file(store, signal_defect(source=source), [UNRELATED])
    assert out["action"] == "filed" and len(r.creates()) == 1
    assert not ev(store, "selfheal_adopted_owner")
    (skip,) = ev(store, "selfheal_adopt_skipped")
    assert skip["candidates"] == [UNRELATED["id"]]


def test_card_naming_the_fingerprint_is_adopted(store):
    d = signal_defect()
    named = {"id": "t_names0fp01", "status": "done", "board": None, "title": "coach nudge loop",
             "body": f"Found while investigating self-heal {d['fingerprint']}."}
    out, r = file(store, d, [UNRELATED, named])
    assert out["action"] == "skip" and out["card"] == "t_names0fp01" and not r.creates()
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["card"] == "t_names0fp01" and e["candidates"] == ["t_names0fp01"]


def test_card_whose_idempotency_key_is_the_fingerprint_is_adopted(store):
    d = signal_defect()
    keyed = {"id": "t_keyed00001", "status": "running", "board": None, "title": "unrelated-looking title",
             "body": "", "idempotency_key": f"selfheal:{d['fingerprint']}"}
    out, r = file(store, d, [keyed])
    assert out["action"] == "skip" and out["card"] == "t_keyed00001" and not r.creates()


def test_card_titled_with_the_truncated_symptom_is_adopted(store):
    titled = {"id": "t_symptom001", "status": "running", "board": None,
              "title": "self-heal: " + SYMPTOM[:100], "body": ""}
    out, r = file(store, signal_defect(), [UNRELATED, titled])
    assert out["action"] == "skip" and out["card"] == "t_symptom001" and not r.creates()


def test_card_sharing_several_distinctive_words_is_adopted(store):
    d = signal_defect(source="backstop", symptom="archived session remains visible in sidebar")
    card = {"id": "t_sidebar001", "status": "running", "board": None,
            "title": "Sidebar keeps showing archived sessions", "body": ""}
    out, r = file(store, d, [card])
    assert out["action"] == "skip" and out["card"] == "t_sidebar001"


def test_user_complaint_still_adopts_the_sessions_card_and_prefers_one_naming_it(store):
    d = signal_defect(source="complaint", symptom="sidebar sessions reshuffle")
    fix = {"id": "t_fixsort001", "status": "running", "board": None, "created_at": NOW - 50,
           "title": "Sidebar sessions reshuffle on every refresh", "body": ""}
    newer = {"id": "t_newer00001", "status": "running", "board": None, "created_at": NOW - 10,
             "title": "Unrelated follow-up", "body": ""}
    out, r = file(store, d, [fix, newer])
    assert out["action"] == "skip" and out["card"] == "t_fixsort001"
    store2 = core.Store(Path(store.root).parent / "coach2")
    out, r = file(store2, d, [newer])  # the session's own card after the user's complaint, unchanged behavior
    assert out["action"] == "skip" and out["card"] == "t_newer00001"


def test_card_names_defect_needs_more_than_one_generic_word():
    d = signal_defect()
    assert not sh.card_names_defect(UNRELATED, d)
    assert sh.card_names_defect({"title": "x", "body": d["fingerprint"]}, d)


def test_session_owned_cards_returns_idempotency_key(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "kanban.db")
    db.executescript("""create table tasks(id text, title text, status text, created_at int, session_id text,
                          body text, idempotency_key text);
        create table kanban_notify_subs(task_id text, chat_id text);
        insert into tasks values('t_1','a','running',100,'S','b','selfheal:agent-x');""")
    db.commit()
    db.close()
    (c,) = sh.session_owned_cards("S", 90, home=tmp_path)
    assert c["idempotency_key"] == "selfheal:agent-x"

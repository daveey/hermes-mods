"""desktop-40fef7fbb1 ("archiving a session repeatedly fails"): the self-heal lane adopted the complaining session's
NEWEST card, "Activate instant hub core updates", filed 4 h after the complaint, as the fix owner. The real fix was
the state.db unconfirmed-read latch card the same session filed 50 minutes after the complaint, whose title names the
root cause, not the symptom. A second fingerprint of the same complaint turn (desktop-e355285fde) then inherited the
wrong owner and its VERIFY was filed against the wrong handoff.

Owner adoption now (1) accepts a card whose body names the session the complaint is about plus a symptom word
(card_answers_complaint), and (2) ranks gated candidates by relevance (owner_relevance), never by recency."""
import importlib.util
import json
import sqlite3
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


sh = load("coach_self_heal_adopt_relevance_test", ROOT / "self_heal.py")
core = load("coach_core_adopt_relevance_test", ROOT / "core.py")

SID = "20261110_185031_4f5f4b"      # the complaining session
ABOUT = "20261110_183233_4a0c3f"    # the session it could not archive
COMPLAINT_AT = 1794336641.0
NOW = 1794362261.0                  # the wrong adoption
HUB = {}
LATCH = "t_01a1c70093dcb359"
ACTIVATE = "t_01a1c7d08c2a75ae"


def card(cid, title, created_at, body="", key=None, fix_meta=False):
    return {"id": cid, "title": title, "status": "done", "board": None, "created_at": created_at,
            "body": body, "idempotency_key": key, "fix_meta": fix_meta}


PART = ("Part {p} of t_01a1c7c587142bb7 (instant hub core updates; plan A approved in chat " + SID + "). "
        "Build exactly this; don't widen it. Final live acceptance is on card F.")
# The candidate set the lane saw at ts 1794362261, newest first as session_owned_cards returns it (bodies trimmed to
# the words that matter: every card the session filed names the session itself).
INCIDENT = [
    card(ACTIVATE, "Activate instant hub core updates and prove the live acceptance (all-at-once test)", 1794353695,
         "Final card of t_01a1c7c587142bb7 (instant hub core updates; plan A approved in chat " + SID + "). "
         "If any acceptance item fails, fix it (or open a linked fix card and park on it).",
         "instant-hub-update:F-activate-and-accept", fix_meta=True),
    card("t_01a1c7d050770341", "hermes-sync: hub instant core-update mode (no batching, 60 s poll, immediate pause, "
         "30 s hold cap, 60 s total cap)", 1794353680, PART.format(p="E"), "instant-hub-update:E-hermes-sync-instant"),
    card("t_01a1c7d04f27d2c9", "Core: a managed Kanban run continues across its owner gateway's restart (no reclaim)",
         1794353680, PART.format(p="D") + " A reclaim fails the run.", "instant-hub-update:D-kanban-continue"),
    card("t_01a1c7d04c1728b2", "Core: cron runs cut off by a gateway restart rerun right after startup", 1794353679,
         PART.format(p="B") + " The run fails silently.", "instant-hub-update:B-cron-reschedule"),
    card("t_01a1c7d04d9046d2", "Core: terminal processes survive a gateway restart (own systemd scope) and reattach",
         1794353679, PART.format(p="C"), "instant-hub-update:C-survivable-processes"),
    card("t_01a1c7cf6f171e47", "Core: update pause cancels a mid-tool turn after 30 s and keeps it resumable",
         1794353622, PART.format(p="A"), "instant-hub-update:A-midtool-cancel"),
    card("t_01a1c7c587142bb7", "Build: instant hub core updates (apply now, checkpoint and resume running work)",
         1794352973, "Plan A approved in chat " + SID + ". Build it without re-asking. If a step fails, stop.",
         "build:instant-hub-updates:20261110"),
    card("t_01a1c77acb74d1be", "core-wt ship: run tests on GitHub CI only (drop the duplicate hub affected-test run)",
         1794348075, "The user asked (chat " + SID + "): why are we testing on the hub and not git-ci workers.",
         "fix:core-wt-ship-tests-on-ci:20261110"),
    card(LATCH, "state.db: unconfirmed read SQLITE_CORRUPT must not latch the process (5s confirm can't cover 11GB "
         "messages)", 1794340066,
         "## Problem\nAt 2026-11-10 18:43:21 on hermes-hub, one pooled read raised SQLITE_CORRUPT. The confirmation "
         "timed out, so hermes_state_health latched and every write in that process was refused. The user's sidebar "
         "archive of " + ABOUT + " failed (session.mutate storage failure ... FATAL: state.db reported structural "
         "corruption), and so did the archives of 20261103_054544_8ab824 and 20261110_184441_da6982.",
         "fix:state-db-read-confirm-timeout-latch:20261110", fix_meta=True),
]


def complaint(**kw):
    d = {"component": "desktop", "symptom": "archiving a session repeatedly fails", "source": "complaint",
         "kind": "hermes_bug", "p_defect": 0.96, "session_id": SID, "host": "hermes-hub", "at": COMPLAINT_AT,
         "quote": f"@session:default/{ABOUT} archiving this session keeps failing, wtf",
         "evidence": f"@session:default/{ABOUT} archiving this session keeps failing, wtf"}
    d.update(kw)
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


def OPEN(board=None, now=None):
    return {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True, "why": ""}


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


@pytest.fixture
def store(tmp_path):
    return core.Store(tmp_path / "coach")


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def file(store, d, cards):
    r = Runner()
    owner_fn = lambda sid, since: [dict(c) for c in cards]  # noqa: E731 - newest first, as session_owned_cards
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN, owner_fn=owner_fn)
    return out, r


def test_incident_fingerprint_is_the_one_the_lane_logged():
    assert complaint()["fingerprint"] == "desktop-40fef7fbb1"


def test_state_db_latch_card_wins_over_the_later_unrelated_activation_card(store):
    out, r = file(store, complaint(), INCIDENT)
    assert out["action"] == "skip" and out["card"] == LATCH and not r.creates()
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["card"] == LATCH and e["candidates"][0] == LATCH
    assert ACTIVATE not in e["candidates"]
    assert sh.LaneState(store.root).get(complaint()["fingerprint"])["fix_card"] == LATCH


def test_relevance_beats_recency_when_the_later_card_also_passes_the_gate(store):
    # Even if the activation card named the archived session, the latch card shares more of the symptom.
    later = dict(INCIDENT[0], body=INCIDENT[0]["body"] + f" Acceptance turn ran in {ABOUT}.")
    d = complaint()
    assert sh.card_answers_complaint(later, d) and sh.card_answers_complaint(INCIDENT[-1], d)
    out, _ = file(store, d, [later] + INCIDENT[1:])
    assert out["card"] == LATCH
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["candidates"][:2] == [LATCH, ACTIVATE]


def test_equally_relevant_cards_go_to_the_earliest_response_not_the_newest():
    d = complaint(symptom="sidebar sessions reshuffle")
    early = card("t_early00001", "Sidebar sessions reshuffle on refresh", COMPLAINT_AT + 60)
    late = card("t_late000001", "Sidebar sessions reshuffle on refresh", COMPLAINT_AT + 9000)
    ranked = sorted([late, early], key=lambda c: sh.owner_relevance(c, d), reverse=True)
    assert [c["id"] for c in ranked] == ["t_early00001", "t_late000001"]


def test_fix_metadata_breaks_a_relevance_tie():
    d = complaint(symptom="sidebar sessions reshuffle")
    plain = card("t_plain00001", "Sidebar sessions reshuffle on refresh", COMPLAINT_AT + 60)
    fixed = card("t_fixed00001", "Sidebar sessions reshuffle on refresh", COMPLAINT_AT + 600, fix_meta=True)
    assert max([plain, fixed], key=lambda c: sh.owner_relevance(c, d))["id"] == "t_fixed00001"


def test_the_complaining_sessions_own_id_is_not_an_anchor(store):
    # Every card the session files names its own chat; that is not evidence it answers the complaint.
    d = complaint(quote="archiving keeps failing, wtf", evidence="archiving keeps failing, wtf")
    assert not any(sh.card_answers_complaint(c, d) for c in INCIDENT)
    out, r = file(store, d, INCIDENT)
    assert out["action"] == "filed" and len(r.creates()) == 1
    assert not ev(store, "selfheal_adopted_owner")


def test_anchor_without_a_symptom_word_is_not_enough():
    d = complaint()
    c = card("t_anchor0001", "Rename the chat", COMPLAINT_AT + 60, body=f"Renamed {ABOUT} to 'notes'.")
    assert not sh.card_answers_complaint(c, d)


def test_session_owned_cards_reports_fix_metadata(tmp_path):
    db = sqlite3.connect(tmp_path / "kanban.db")
    db.executescript("""create table tasks(id text, title text, status text, created_at int, session_id text,
                          body text, idempotency_key text, result text);
        create table kanban_notify_subs(task_id text, chat_id text);
        create table task_runs(id integer primary key, task_id text, metadata text);
        insert into tasks values('t_fix','a','done',100,'S','b',null,null);
        insert into tasks values('t_res','a','done',101,'S','b',null,'{"landed_sha": "abc"}');
        insert into tasks values('t_none','a','running',102,'S','b',null,null);
        insert into task_runs(task_id, metadata) values('t_fix','{"published_pr": "https://example.com/pr/1"}');
        insert into task_runs(task_id, metadata) values('t_none','{"tests_run": 3}');""")
    db.commit()
    db.close()
    got = {c["id"]: c["fix_meta"] for c in sh.session_owned_cards("S", 90, home=tmp_path)}
    assert got == {"t_fix": True, "t_res": True, "t_none": False}


def test_session_owned_cards_without_runs_table_still_reads(tmp_path):
    db = sqlite3.connect(tmp_path / "kanban.db")
    db.executescript("""create table tasks(id text, title text, status text, created_at int, session_id text);
        create table kanban_notify_subs(task_id text, chat_id text);
        insert into tasks values('t_1','a','running',100,'S');""")
    db.commit()
    db.close()
    (c,) = sh.session_owned_cards("S", 90, home=tmp_path)
    assert c["fix_meta"] is False

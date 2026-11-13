"""self-heal: an agent-driven turn is not a user complaint (agent-d925f95adc, kanban-1bac72b21a).

A Kanban worker drove a throwaway Desktop proof chat and wrote its steering turn ("Use the explicitly requested
original regression path: same CLI block command with --metadata-only..."); coach judged it as David's complaint and
filed two defects. `user_facing()` looked only at platform, parent session and HERMES_KANBAN_TASK. These tests pin:
  - core's `input_origin="agent"` (a ?client=agent transport, an agent-created or hidden submit) skips the judge
    with a `selfheal_judged skipped why=agent_input` row, and no detect;
  - on a core that passes no origin, a hidden or scratch state.db row skips with `why=agent_session`, both before
    the judge (new __init__) and in handle() (a backend still on an older __init__);
  - the lane drops agent-origin / hidden payloads (`selfheal_skip why=agent_input`), and the digest backstop skips
    hidden sessions;
  - a visible human turn is still judged.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_self_heal_judged import ASK, PREV, SID, events, plugin, sh  # noqa: E402
from test_self_heal_judged import store  # noqa: E402,F401  (fixture)

HIT = json.dumps({"p_defect": 0.96, "kind": "agent_fault", "component": "agent",
                  "symptom": "omitted requested CLI flag"})


def hooks(store, llm):
    _, h = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm, selfheal_confirm=None)
    return h


def turn(h, **kw):
    h["pre_llm_call"](session_id=SID, user_message=ASK, platform="desktop",
                      conversation_history=[{"role": "assistant", "content": PREV}], **kw)


def state_row(store, hidden=0, source="desktop", sid=SID):
    home = Path(store.root).parent
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, "
               "hidden INTEGER NOT NULL DEFAULT 0)")
    db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
               "content TEXT, timestamp REAL)")
    db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?)", (sid, source, hidden))
    db.commit()
    db.close()


def no_judge(s, p):
    pytest.fail("judge must not run on an agent-driven turn")


def test_agent_origin_desktop_turn_is_skipped_before_the_judge(store):
    turn(hooks(store, no_judge), input_origin="agent")
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "skipped" and row["why"] == "agent_input"
    assert events(store, "selfheal_detect") == []


@pytest.mark.parametrize("hidden,source", [(1, "desktop"), (0, "scratch")])
def test_hidden_or_scratch_row_is_skipped_on_a_core_without_origin(store, hidden, source):
    state_row(store, hidden=hidden, source=source)
    turn(hooks(store, no_judge))
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "skipped" and row["why"] == "agent_session"
    assert events(store, "selfheal_detect") == []


def test_older_init_hidden_row_still_files_nothing_via_handle(store):
    state_row(store, hidden=1)
    hit = sh.detect_complaint(PREV, ASK, llm=lambda s, p: HIT, threshold=sh.threshold())
    assert hit
    filed = []
    sh.handle(hit, SID, store, evidence=ASK, platform="desktop", ask=ASK, filer=lambda d, **k: filed.append(d))
    assert filed == [] and events(store, "selfheal_detect") == []
    assert [r["why"] for r in events(store, "selfheal_skip")] == ["agent_session"]


@pytest.mark.parametrize("origin", ["human", ""])
def test_visible_human_turn_is_still_judged(store, origin):
    state_row(store, hidden=0)
    turn(hooks(store, lambda s, p: HIT), **({"input_origin": origin} if origin else {}))
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "hit"
    (det,) = events(store, "selfheal_detect")
    assert det["source"] == "complaint"


def test_agent_origin_admission_turn_is_not_audited(store):
    calls = []
    _, h = plugin.build(store=store, checkout=None, selfheal_sync=True,
                        selfheal_llm=lambda s, p: calls.append(p) or HIT, selfheal_confirm=None)
    reply = "Sorry, I made a mistake: I skipped the --metadata-only flag you asked for."
    h["post_llm_call"](session_id=SID, user_message=ASK, platform="desktop", input_origin="agent",
                       assistant_response=reply, conversation_history=[])
    assert calls == [] and events(store, "selfheal_detect") == []
    # control: the same reply on a human turn is audited
    h["post_llm_call"](session_id=SID + "-h", user_message=ASK, platform="desktop", input_origin="human",
                       assistant_response=reply, conversation_history=[])
    assert calls


def test_user_facing_and_skip_reason_know_agent_origin():
    assert sh.user_facing("desktop", "", input_origin="agent") is False
    assert sh.skip_reason("desktop", "", input_origin="agent") == "agent_input"
    assert sh.user_facing("desktop", "", input_origin="human") is True
    assert sh.user_facing("desktop", "") is True  # old core: unknown origin is judged as before


# ── lane: payloads from hosts still on old code ──
@pytest.mark.parametrize("extra", [{"input_origin": "agent"}, {"hidden": True}])
def test_lane_drops_agent_origin_payloads(store, extra):
    d = {"fingerprint": "agent-d925f95adc", "component": "agent", "symptom": "omitted requested CLI flag",
         "source": "complaint", "kind": "complaint", "p_defect": 0.96, "session_id": SID, "quote": ASK,
         "host": "macbook", "at": 1793590440.0, **extra}
    out = sh.file_defect(d, store=store, m="shadow", cfg={})
    assert out == {"action": "skip", "why": "agent_input"}
    assert [r["why"] for r in events(store, "selfheal_skip")] == ["agent_input"]
    assert events(store, "selfheal_would_file") == []


def test_backstop_skips_hidden_digest_sessions(store, tmp_path):
    import time
    now = time.time()
    data = tmp_path / "digests"
    for sid, hidden in (("hidden-sid", True), ("visible-sid", False)):
        p = data / "macbook" / "default" / f"{sid}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"id": sid, "source": "desktop", "hidden": hidden, "host": "macbook",
                                 "user_turns": [{"t": now - 60, "text": ASK, "prev": PREV}]}))
    judged = []

    def judge(s, p):
        judged.append(p)
        return HIT
    sh.backstop(data, store=store, judge=judge, now=now, filer=lambda d, **k: d, m="shadow", confirm=None)
    sids = [r["session_id"] for r in events(store, "selfheal_detect")]
    assert sids == ["visible-sid"]
    skips = [r for r in events(store, "selfheal_skip") if r.get("why") == "agent_input"]
    assert [r["session_id"] for r in skips] == ["hidden-sid"]
    # The cursor advanced: a second pass logs no new skip row.
    sh.backstop(data, store=store, judge=judge, now=now, filer=lambda d, **k: d, m="shadow", confirm=None)
    assert len([r for r in events(store, "selfheal_skip") if r.get("why") == "agent_input"]) == 1

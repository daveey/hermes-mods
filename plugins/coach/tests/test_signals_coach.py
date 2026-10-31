"""A coach nudge whose turn was killed (gateway restart) is not an agent defect (self-heal agent-fb58ec3a8f).

The reply_nudge_outcome is written only when the next final reply runs. A turn interrupted after the nudge never
gets one, so collect_coach used to file it after HANDOFF_GRACE_S as "agent's revised reply still failed". Now it
reads the session's messages after the nudge: an interruption marker with no later final reply drops the signal and
logs nudge_outcome_lost; anything else files with a truthful "no outcome" symptom; an unreadable db files (fail-open).
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("coach_signals_nudge_lost_test", ROOT / "signals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coach_signals_nudge_lost_test"] = mod
    spec.loader.exec_module(mod)
    return mod


sg = _load()
NOW = 1792660000.0
SID = "local-abc123"
NUDGE = {"ts": NOW, "event": "reply_nudge", "session_id": SID, "rule": "report_only", "p": 0.88,
         "evidence": "Final leaves CI/local verification pending", "draft_id": "d1", "outcome": True}
LATE = NOW + sg.HANDOFF_GRACE_S + 1


def _home(tmp_path, messages=None, db=True):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text(json.dumps(NUDGE) + "\n")
    if db:
        c = sqlite3.connect(h / "state.db")
        c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
                  "tool_calls text, timestamp real, active integer default 1, compacted integer default 0)")
        for i, (role, content, tool_calls, dt) in enumerate(messages or []):
            c.execute("insert into messages (session_id, role, content, tool_calls, timestamp) values (?,?,?,?,?)",
                      (SID, role, content, tool_calls, NOW + dt))
        c.commit()
        c.close()
    return h


def _coach(sigs):
    return [s for s in sigs if s["kind"] == "coach"]


def _log(h):
    return [json.loads(l) for l in (h / "coach" / "log.jsonl").read_text().splitlines()]


INTERRUPTED = [("assistant", "", '[{"name": "terminal"}]', 30),
               ("tool", "33 passed", None, 200),
               ("assistant", "Operation interrupted: waiting for model response (6.9s elapsed).", None, 226),
               ("assistant", "An automated delivery was interrupted and was not run again. Some actions may already "
                             "have run; verify their effects.", None, 228)]


def test_nudge_whose_turn_was_interrupted_files_nothing_and_logs_outcome_lost(tmp_path):
    h = _home(tmp_path, INTERRUPTED)
    cur = {}
    assert _coach(sg.collect_coach(h, cur, NOW + 60)) == []  # still inside the grace window
    assert _coach(sg.collect_coach(h, cur, LATE)) == []
    lost = [r for r in _log(h) if r.get("event") == "nudge_outcome_lost"]
    assert len(lost) == 1 and lost[0]["session_id"] == SID and lost[0]["reason"] == "interrupted"
    assert lost[0]["rule"] == "report_only"
    assert _coach(sg.collect_coach(h, cur, LATE + 3600)) == []  # forgotten, never filed later


def test_interrupt_followed_by_a_final_reply_still_files(tmp_path):
    h = _home(tmp_path, INTERRUPTED + [("assistant", "Done: changelog test fixed and pushed.", None, 400)])
    got = _coach(sg.collect_coach(h, {}, LATE))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]


def test_nudge_with_a_normal_later_reply_and_no_outcome_files_with_the_no_outcome_symptom(tmp_path):
    h = _home(tmp_path, [("assistant", "", '[{"name": "terminal"}]', 30),
                         ("assistant", "Here is the revised summary.", None, 90)])
    got = _coach(sg.collect_coach(h, {}, LATE))
    assert len(got) == 1 and got[0]["session_id"] == SID
    assert "nudge got no outcome (no revised reply)" in got[0]["symptom"]
    assert "revised reply still failed" not in got[0]["symptom"]
    assert not [r for r in _log(h) if r.get("event") == "nudge_outcome_lost"]


def test_unreadable_state_db_files_as_before(tmp_path):
    h = _home(tmp_path, db=False)
    (h / "state.db").write_text("not a sqlite database")
    got = _coach(sg.collect_coach(h, {}, LATE))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]


def test_superseded_compaction_copies_are_ignored(tmp_path):
    """An interrupt marker only in a superseded (active=0, compacted=0) row is not the live transcript."""
    h = _home(tmp_path, INTERRUPTED)
    c = sqlite3.connect(h / "state.db")
    c.execute("update messages set active=0 where content like '%interrupted%'")
    c.commit()
    c.close()
    assert len(_coach(sg.collect_coach(h, {}, LATE))) == 1


def test_session_in_a_profile_state_db_is_found(tmp_path):
    h = _home(tmp_path, [])
    prof = h / "profiles" / "alpha"
    prof.mkdir(parents=True)
    src = sqlite3.connect(h / "state.db")
    c = sqlite3.connect(prof / "state.db")
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
              "tool_calls text, timestamp real, active integer default 1, compacted integer default 0)")
    for role, content, tc, dt in INTERRUPTED:
        c.execute("insert into messages (session_id, role, content, tool_calls, timestamp) values (?,?,?,?,?)",
                  (SID, role, content, tc, NOW + dt))
    c.commit()
    c.close()
    src.close()
    assert _coach(sg.collect_coach(h, {}, LATE)) == []


# agent-f668487724: past the report_only confirm cap an unconfirmed report_only no longer nudges, so a capped day is a
# blind spot. Any report_only_confirm_capped row files one signal per day (later days comment on the same card).
def test_confirm_cap_reached_files_one_signal_per_day(tmp_path):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    rows = [{"ts": NOW + i, "event": "report_only_confirm_capped", "session_id": f"s{i}", "used": 250 + i, "cap": 250}
            for i in range(3)]
    (h / "coach" / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    cur = {}
    got = [s for s in sg.collect_coach(h, cur, NOW + 60) if "confirm" in s["fp"] or "cap" in s["symptom"]]
    assert len(got) == 1 and got[0]["component"] == "plugin" and got[0]["comment_open_card"] is True
    assert "used=250 cap=250" in got[0]["details"]
    # same day again: nothing new
    with open(h / "coach" / "log.jsonl", "a") as f:
        f.write(json.dumps({**rows[0], "ts": NOW + 120}) + "\n")
    assert [s for s in sg.collect_coach(h, cur, NOW + 180) if "cap reached" in s["symptom"]] == []
    # a later day: one more signal with the same fingerprint (comments on the open card)
    with open(h / "coach" / "log.jsonl", "a") as f:
        f.write(json.dumps({**rows[0], "ts": NOW + 86400 * 2}) + "\n")
    again = [s for s in sg.collect_coach(h, cur, NOW + 86400 * 2 + 60) if "cap reached" in s["symptom"]]
    assert len(again) == 1 and again[0]["fp"] == got[0]["fp"]
    d = sg.to_defect(again[0])
    assert d["component"] == "plugin" and d.get("comment_open_card") is True


def test_no_capped_rows_files_nothing(tmp_path):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text(json.dumps({"ts": NOW, "event": "report_only_confirm", "ok": True}) + "\n")
    assert [s for s in sg.collect_coach(h, {}, NOW + 60) if "cap reached" in s["symptom"]] == []

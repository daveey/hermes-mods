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


# agent-c46bf512ae: the nudged turn died on a core failed-turn boundary ("This turn did not complete..."), which
# _turn_lost did not know, and a LATER notice turn's final reply reset the verdict, so it filed as an agent ignoring
# the nudge. Replays the incident's shape: tool rows, the failed_turn row, then a different (user-started) turn.
PARTIAL = "This turn did not complete. Some actions may already have run; verify their effects before resending."


def _home_typed(tmp_path, messages):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text(json.dumps(NUDGE) + "\n")
    c = sqlite3.connect(h / "state.db")
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
              "tool_calls text, timestamp real, active integer default 1, compacted integer default 0, "
              "display_kind text)")
    for role, content, tool_calls, dt, kind in messages:
        c.execute("insert into messages (session_id, role, content, tool_calls, timestamp, display_kind) "
                  "values (?,?,?,?,?,?)", (SID, role, content, tool_calls, NOW + dt, kind))
    c.commit()
    c.close()
    return h


FAILED_TURN_INCIDENT = [("assistant", "", '[{"name": "kanban_show"}]', 5, None),
                        ("tool", "{...}", None, 6, None),
                        ("assistant", PARTIAL, None, 20, "failed_turn"),
                        # 23:49: a different turn (a Kanban notice) ran and answered
                        ("user", "[Kanban notice] t_123 completed", None, 200, None),
                        ("assistant", "Noted: t_123 is done.", None, 230, None)]


def test_incident_failed_turn_row_is_lost_not_an_ignored_nudge(tmp_path):
    h = _home_typed(tmp_path, FAILED_TURN_INCIDENT)
    assert _coach(sg.collect_coach(h, {}, LATE)) == []
    lost = [r for r in _log(h) if r.get("event") == "nudge_outcome_lost"]
    assert len(lost) == 1 and lost[0]["reason"] == "failed_turn" and lost[0]["session_id"] == SID


def test_untyped_failed_turn_notice_text_is_lost_too(tmp_path):
    rows = [(r, (c if k != "failed_turn" else c), t, dt, None) for r, c, t, dt, k in FAILED_TURN_INCIDENT]
    h = _home_typed(tmp_path, rows)
    assert _coach(sg.collect_coach(h, {}, LATE)) == []


def test_reply_in_the_nudged_turn_after_a_tool_still_files(tmp_path):
    """No failed boundary: the nudged turn answered (without an outcome line) -> files as before."""
    h = _home_typed(tmp_path, [("assistant", "", '[{"name": "terminal"}]', 5, None),
                               ("assistant", "Here is the revised summary.", None, 60, None)])
    got = _coach(sg.collect_coach(h, {}, LATE))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]


# ── self-heal agent-98f77b1ca9: a nudge carrying a nudge_id is matched by id, not by a 15-min window ────────────
# The report_only nudge's turn spent 918 s doing the fix; its delivery (+918 s) and resolved outcome (+926 s) were
# logged after HANDOFF_GRACE_S, so the collector filed "no outcome" for a nudge that worked.
NID = "n_synthetic98f7"
ID_NUDGE = {**NUDGE, "nudge_id": NID}
DELIVERED = {"ts": NOW + 918, "event": "reply_nudge_delivered", "session_id": SID, "rule": "report_only",
             "nudge_id": NID, "via": "continuation"}
RESOLVED = {"ts": NOW + 926, "event": "reply_nudge_outcome", "session_id": SID, "rule": "report_only",
            "draft_id": "d1", "nudge_id": NID, "resolved": True, "p": 0.1, "reply": "Fixed and verified."}
WORKING = [("assistant", "", '[{"name": "terminal"}]', 30), ("tool", "ok", None, 400)]


def _append(h, *rows):
    with (h / "coach" / "log.jsonl").open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _id_home(tmp_path, messages=WORKING):
    h = _home(tmp_path, messages)
    (h / "coach" / "log.jsonl").write_text(json.dumps(ID_NUDGE) + "\n")
    return h


def test_id_nudge_with_outcome_after_the_grace_files_nothing(tmp_path):
    h = _id_home(tmp_path)
    cur = {}
    assert _coach(sg.collect_coach(h, cur, NOW + 910)) == []  # past 900 s, still undelivered: pending
    _append(h, DELIVERED, RESOLVED)
    assert _coach(sg.collect_coach(h, cur, NOW + 930)) == []
    assert not cur.get("coach_handoff_pending")
    assert _coach(sg.collect_coach(h, cur, NOW + sg.NUDGE_OPEN_S + 60)) == []


def test_id_outcome_matches_by_id_with_no_time_limit():
    late = {**RESOLVED, "ts": NOW + 5000}
    assert sg._outcome_matches({**ID_NUDGE}, late)
    assert not sg._outcome_matches({**ID_NUDGE}, {**late, "nudge_id": "n_other"})
    assert not sg._outcome_matches(dict(NUDGE), {**late, "nudge_id": None})  # no id: 15-min window


def test_undelivered_id_nudge_with_a_live_turn_waits_until_nudge_open(tmp_path):
    h = _id_home(tmp_path)
    cur = {}
    assert _coach(sg.collect_coach(h, cur, NOW + 20 * 60)) == []
    assert cur.get("coach_handoff_pending")
    got = _coach(sg.collect_coach(h, cur, NOW + sg.NUDGE_OPEN_S + 1))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]


def test_delivered_id_nudge_files_grace_after_delivery(tmp_path):
    h = _id_home(tmp_path)
    cur = {}
    _append(h, DELIVERED)
    assert _coach(sg.collect_coach(h, cur, NOW + 918 + sg.HANDOFF_GRACE_S - 5)) == []
    got = _coach(sg.collect_coach(h, cur, NOW + 918 + sg.HANDOFF_GRACE_S + 5))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]


def test_id_nudge_whose_turn_died_is_still_closed_as_lost_at_the_grace(tmp_path):
    h = _id_home(tmp_path, INTERRUPTED)
    assert _coach(sg.collect_coach(h, {}, LATE)) == []
    assert [r for r in _log(h) if r.get("event") == "nudge_outcome_lost" and r.get("nudge_id") == NID]


def test_no_id_nudge_keeps_the_15_min_rule(tmp_path):
    h = _home(tmp_path, WORKING)
    cur = {}
    assert _coach(sg.collect_coach(h, cur, NOW + 600)) == []
    _append(h, {**RESOLVED, "nudge_id": None, "ts": NOW + 926})
    got = _coach(sg.collect_coach(h, cur, NOW + 930))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]


def test_guard_finds_an_outcome_the_matcher_missed(tmp_path):
    """Detection guard: before filing no-outcome, the log is re-read for an outcome with the same nudge_id."""
    h = _id_home(tmp_path)
    cur = {}
    _append(h, DELIVERED)
    sg.collect_coach(h, cur, NOW + 920)  # reads the delivery; the outcome below lands in an already-read offset
    _append(h, {**RESOLVED, "rule": "confirm"})  # a rule mismatch: the matcher can't pair it
    assert _coach(sg.collect_coach(h, cur, NOW + 918 + sg.HANDOFF_GRACE_S + 5)) == []
    assert [r for r in _log(h) if r.get("event") == "nudge_outcome_late" and r.get("nudge_id") == NID]


# agent-a8d0c8c1ed: a Hermes update paused the nudged turn at a step boundary and restarted the backend (its open
# nudges lived only in memory). The turn resumed on core's pause note (tui_gateway/update_pause.py pause_note) and
# finished, but no outcome could come any more, so collect_coach filed "nudge got no outcome". Replays that shape.
PAUSE_NOTE = ("[System note: Your previous turn was interrupted mid-run \u2014 it was paused at a step boundary for a "
              "Hermes update. Every tool result above is complete; continue with the next step and don't redo "
              "finished steps. The paused request was:]\n\n")
RESUMED = [("assistant", "", '[{"name": "kanban_show"}]', 20),
           ("tool", "{...}", None, 21),
           ("user", PAUSE_NOTE + "[Kanban notice] t_1 completed", None, 240),
           ("assistant", "", '[{"name": "patch"}]', 260),
           ("tool", "ok", None, 261),
           ("assistant", "", '[{"name": "terminal"}]', 300),
           ("tool", "12 passed", None, 330),
           ("assistant", "Fixed bb_bridge.py, pushed it and checked the mini.", None, 600)]


def _lost(h):
    return [r for r in _log(h) if r.get("event") == "nudge_outcome_lost"]


def test_incident_turn_resumed_after_update_pause_is_lost_not_ignored(tmp_path):
    h = _home(tmp_path, RESUMED)
    cur = {}
    assert _coach(sg.collect_coach(h, cur, LATE)) == []
    lost = _lost(h)
    assert len(lost) == 1 and lost[0]["reason"] == "resumed_after_restart" and lost[0]["session_id"] == SID
    assert _coach(sg.collect_coach(h, cur, LATE + 3600)) == []
    assert len(_lost(h)) == 1  # closed once, never refiled


def test_kanban_notice_row_with_the_resume_note_appended_is_lost_too(tmp_path):
    rows = [(r, ("[Kanban notice] t_2 blocked\n\n" + PAUSE_NOTE + "go on") if r == "user" else c, t, dt)
            for r, c, t, dt in RESUMED]
    h = _home(tmp_path, rows)
    assert _coach(sg.collect_coach(h, {}, LATE)) == []
    assert [r["reason"] for r in _lost(h)] == ["resumed_after_restart"]


def test_resume_note_in_a_later_turn_after_the_nudged_reply_still_files(tmp_path):
    """The nudged turn answered (no outcome logged); a later turn was paused: the nudge still files."""
    h = _home(tmp_path, [("assistant", "Here is the revised summary.", None, 60),
                         ("user", "thanks, now the next thing", None, 120),
                         ("assistant", "", '[{"name": "terminal"}]', 130),
                         ("user", PAUSE_NOTE + "thanks, now the next thing", None, 300),
                         ("assistant", "Done.", None, 400)])
    got = _coach(sg.collect_coach(h, {}, LATE))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]


def test_ordinary_next_ask_with_no_outcome_still_files_after_grace(tmp_path):
    h = _home(tmp_path, [("assistant", "", '[{"name": "terminal"}]', 30),
                         ("user", "also check the mini please", None, 200),
                         ("assistant", "Checked the mini.", None, 260)])
    cur = {}
    assert _coach(sg.collect_coach(h, cur, NOW + 60)) == []
    got = _coach(sg.collect_coach(h, cur, LATE))
    assert len(got) == 1 and "nudge got no outcome (no revised reply)" in got[0]["symptom"]
    assert _lost(h) == []


def _dead_pid():
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _home_nudge(tmp_path, nudge, messages):
    h = _home(tmp_path, messages)
    (h / "coach" / "log.jsonl").write_text(json.dumps(nudge) + "\n")
    return h


ANSWERED = [("assistant", "", '[{"name": "terminal"}]', 30), ("assistant", "Here is the revised summary.", None, 90)]


def test_nudge_from_a_backend_that_is_gone_is_lost(tmp_path):
    n = {**NUDGE, "pid": _dead_pid(), "host": sg.short_host()}
    h = _home_nudge(tmp_path, n, ANSWERED)
    assert _coach(sg.collect_coach(h, {}, LATE)) == []
    assert [r["reason"] for r in _lost(h)] == ["backend_restarted"]


def test_nudge_whose_pid_now_belongs_to_a_newer_process_is_lost(tmp_path):
    """Restart that reused the pid: the live process started after the nudge (NOW lies in the past)."""
    import os
    n = {**NUDGE, "pid": os.getpid(), "host": sg.short_host()}
    h = _home_nudge(tmp_path, n, ANSWERED)
    assert _coach(sg.collect_coach(h, {}, LATE)) == []
    assert [r["reason"] for r in _lost(h)] == ["backend_restarted"]


def test_nudge_from_the_still_running_backend_files(tmp_path):
    import os
    import time as _t
    now = _t.time()
    n = {**NUDGE, "ts": now, "pid": os.getpid(), "host": sg.short_host(), "proc_start": sg.proc_start(os.getpid())}
    h = _home(tmp_path)
    (h / "coach" / "log.jsonl").write_text(json.dumps(n) + "\n")
    got = _coach(sg.collect_coach(h, {}, now + sg.HANDOFF_GRACE_S + 1))
    assert len(got) == 1 and "no outcome" in got[0]["symptom"]
    assert _lost(h) == []


def test_nudge_stamped_on_another_host_skips_the_pid_check(tmp_path):
    n = {**NUDGE, "pid": _dead_pid(), "host": "some-other-host"}
    h = _home_nudge(tmp_path, n, ANSWERED)
    assert len(_coach(sg.collect_coach(h, {}, LATE))) == 1


def test_open_nudge_of_a_gone_backend_is_closed_lost(tmp_path):
    """close_open_nudges uses the same verdict: the open record gets its delivery + outcome once."""
    n = {**NUDGE, "nudge_id": "n_gone", "pid": _dead_pid(), "host": sg.short_host()}
    h = _home_nudge(tmp_path, n, ANSWERED)
    cur = {}
    sg.collect_coach(h, cur, LATE)
    recs = [r for r in _log(h) if r.get("nudge_id") == "n_gone" and r.get("event") != "reply_nudge"]
    assert sorted(r["event"] for r in recs) == ["nudge_outcome_lost", "reply_nudge_undelivered"]
    assert not cur.get("coach_nudge_open")


def test_proc_start_matches_this_process():
    import os
    import time as _t
    st = sg.proc_start(os.getpid())
    assert st is not None and st <= _t.time() + 1
    assert sg.proc_start(_dead_pid()) is None


def test_store_stamps_backend_identity_on_reply_nudge(tmp_path):
    import os
    spec = importlib.util.spec_from_file_location("coach_core_stamp_test", ROOT / "core.py")
    core = importlib.util.module_from_spec(spec)
    sys.modules["coach_core_stamp_test"] = core
    spec.loader.exec_module(core)
    st = core.Store(root=tmp_path)
    st.log({"event": "reply_nudge", "session_id": "s", "rule": "report_only"})
    st.log({"event": "reply_checked", "session_id": "s"})
    a, b = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert a["pid"] == os.getpid() and a["host"] == sg.short_host()
    assert abs(a["proc_start"] - sg.proc_start(os.getpid())) <= 2
    assert "pid" not in b

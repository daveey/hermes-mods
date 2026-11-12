"""refusals signal: 2+ consecutive safety-filter refusals of a chat's user asks (self-heal agent-2e0c97e3a0).

Chat 20261102_175427_594451: three harmless questions in a row came back as refusals because the chat's earlier
history tripped the filter. Nothing watched for it; David noticed. The collector files one signal per session.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("coach_signals_refusal_streak_test", ROOT / "signals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coach_signals_refusal_streak_test"] = mod
    spec.loader.exec_module(mod)
    return mod


sg = _load()
NOW = 1792660000.0
SID = "20261102_175427_594451"


def _db(h: Path, rows, *, display_kind=True):
    h.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(h / "state.db")
    cols = "display_kind text, " if display_kind else ""
    c.execute(f"create table messages (id integer primary key, session_id text, role text, content text, "
              f"timestamp real, {cols}observed integer default 0, _compressed_summary integer not null default 0)")
    for sid, role, content, kind, dt in rows:
        if display_kind:
            c.execute("insert into messages (session_id, role, content, timestamp, display_kind) values (?,?,?,?,?)",
                      (sid, role, content, NOW + dt, kind))
        else:
            c.execute("insert into messages (session_id, role, content, timestamp) values (?,?,?,?)",
                      (sid, role, content, NOW + dt))
    c.commit()
    c.close()


def _add(h: Path, rows):
    c = sqlite3.connect(h / "state.db")
    for sid, role, content, kind, dt in rows:
        c.execute("insert into messages (session_id, role, content, timestamp, display_kind) values (?,?,?,?,?)",
                  (sid, role, content, NOW + dt, kind))
    c.commit()
    c.close()


R = "content_refused"
INCIDENT = [
    (SID, "user", "plan the deploy", None, -600),
    (SID, "assistant", "here is the plan", None, -590),
    (SID, "user", "should we do it on the mini or the hub?", R, -120),
    (SID, "user", "does it run on the mac0mini or the hub?", R, -60),
    (SID, "user", "does it run on the mac0mini or the hub? and why", R, -10),
]


def _refusals(sigs):
    return [s for s in sigs if s["kind"] == "refusals"]


def test_consecutive_refusals_file_one_signal_per_session(tmp_path):
    h = tmp_path / "home"
    _db(h, INCIDENT)
    cur = {}
    sigs = _refusals(sg.collect(h, cur, "hubhost", NOW))
    assert len(sigs) == 1
    s = sigs[0]
    assert s["session_id"] == SID and s["component"] == "agent"
    assert "3 times in a row" in s["symptom"]
    assert s["fp"] == sg.fp("agent", "refusal_streak", SID)
    d = sg.to_defect(s)
    assert d["session_id"] == SID and d["fingerprint"] == s["fp"]
    # Next pass: nothing new. A further refusal in the same chat does not refile it.
    assert _refusals(sg.collect(h, cur, "hubhost", NOW + 60)) == []
    _add(h, [(SID, "user", "hello?", R, 30)])
    assert _refusals(sg.collect(h, cur, "hubhost", NOW + 120)) == []


def test_a_single_refusal_or_an_answer_between_refusals_is_not_a_streak(tmp_path):
    h = tmp_path / "home"
    _db(h, [
        ("a", "user", "q1", R, -100), ("a", "user", "q2", None, -90), ("a", "assistant", "answer", None, -80),
        ("b", "user", "q1", R, -50), ("b", "user", "q2", None, -40), ("b", "user", "q3", R, -30),
    ])
    assert _refusals(sg.collect(h, {}, "hubhost", NOW)) == []


def test_old_refusals_before_the_backfill_window_are_not_filed(tmp_path):
    h = tmp_path / "home"
    old = -sg.BACKFILL_S - 3600
    _db(h, [("old", "user", "q1", R, old), ("old", "user", "q2", R, old + 1)])
    assert _refusals(sg.collect(h, {}, "hubhost", NOW)) == []


def test_a_db_without_display_kind_is_skipped_not_a_collector_crash(tmp_path):
    h = tmp_path / "home"
    _db(h, [(SID, "user", "q", None, -5)], display_kind=False)
    sigs = sg.collect(h, {}, "hubhost", NOW)
    assert not [s for s in sigs if s["kind"] in ("refusals", "collector")]


RETRY = ("[Hermes: automatic retry of 1 automated delivery that failed earlier in this chat because the provider "
         "refused it]")


def test_a_streak_of_automated_delivery_retries_is_not_called_a_user_re_ask(tmp_path):
    # Hub chats 20261110_220455_fb17fe / 20261112_034756_e957f3: every refused row was core's automatic retry of a
    # failed automated delivery, so "the user re-asked" sent the fixer looking for a human re-ask that never happened.
    h = tmp_path / "home"
    _db(h, [
        ("auto", "user", RETRY, R, -120), ("auto", "user", RETRY, R, -60),
        ("mixed", "user", RETRY, R, -50), ("mixed", "user", "still there?", R, -40),
    ])
    sigs = {s["session_id"]: s for s in _refusals(sg.collect(h, {}, "hubhost", NOW))}
    assert set(sigs) == {"auto", "mixed"}  # still one stuck-chat signal per session
    auto = sigs["auto"]["symptom"]
    assert "user re-asked" not in auto
    assert "automated deliveries" in auto and "2 times in a row" in auto
    assert sigs["auto"]["fp"] == sg.fp("agent", "refusal_streak", "auto")
    # One human ask in the run keeps the user wording.
    assert "the user re-asked" in sigs["mixed"]["symptom"]


CLOSEOUT = "[chat-closeout] Every kanban card this chat handed work to has settled. Close out this chat now."
NOTICE = "⏳ [default] @default Kanban t_01a1cdea4b8f2339: waiting on a parent no worker has claimed"


def test_the_refused_original_delivery_then_its_retry_is_automated(tmp_path):
    # The same two hub chats, as stored: the run opens with the ORIGINAL automated delivery the provider refused (a
    # chat-closeout prompt / a kanban notice), which never carries the retry prefix; core's retry of it follows.
    # b286f606 required the prefix on every row, so both chats still read "the user re-asked".
    h = tmp_path / "home"
    _db(h, [
        ("closeout", "user", CLOSEOUT, R, -300), ("closeout", "user", RETRY + "\n\n" + CLOSEOUT, R, -200),
        ("notice", "user", NOTICE, R, -150), ("notice", "user", RETRY + "\n\n" + NOTICE, R, -100),
        ("resumed", "user", "[System note: Your previous turn was interrupted mid-run.] go on", R, -95),
        ("resumed", "user", "✔ [default] @default Kanban t_01a1cd000000000 done — fix x", R, -94),
        ("human", "user", NOTICE, R, -90), ("human", "user", "why does this keep failing?", R, -80),
        ("quoted", "user", "what does ⏳ [default] @default Kanban t_1 mean?", R, -70), ("quoted", "user", RETRY, R, -60),
    ])
    sigs = {s["session_id"]: s for s in _refusals(sg.collect(h, {}, "hubhost", NOW))}
    assert set(sigs) == {"closeout", "notice", "resumed", "human", "quoted"}
    for sid in ("closeout", "notice", "resumed"):
        assert "automated deliveries were refused" in sigs[sid]["symptom"], sid
        assert "user re-asked" not in sigs[sid]["symptom"], sid
    # A human ask anywhere in the run, even one quoting a notice, keeps the user wording.
    for sid in ("human", "quoted"):
        assert "the user re-asked" in sigs[sid]["symptom"], sid

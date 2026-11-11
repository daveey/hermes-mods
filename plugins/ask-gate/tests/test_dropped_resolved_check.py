"""A human_request whose resolved_check core stopped probing (resolved_check_dropped_at / _drop_reason): the filed
action may promise "I'll notice automatically"; that promise is historical and the digest must say so."""
import json
import sqlite3

ASK = "Approve the rollout, then I will notice automatically and resume. Unblock = go; archive = drop."
DROPPED = {"action": ASK, "why_human": "needs a person", "resolved_check_dropped_at": 1700000000,
           "resolved_check_drop_reason": "resolved_check_broken"}


def _db(tmp_path, request):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("""create table tasks(id text, title text, status text, assignee text, created_at real,
        completed_at real, result text, session_id text, block_kind text, human_request text);
        create table kanban_notify_subs(task_id text, platform text, chat_id text);""")
    c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
              ("t_00000000dropped", "Rollout", "blocked", "default", 100.0, None, "", "chat", "needs_input",
               json.dumps(request)))
    c.commit()
    c.close()
    return db


def test_card_ask_marks_drop_and_keeps_filed_text_historical(gate):
    out = gate.core.card_ask({"human_request": DROPPED})
    assert "Automatic detection stopped" in out and "probe kept failing" in out
    assert "manual unblock" in out and "re-filed" in out
    assert "historical" in out and "notice automatically" in out  # old prose kept, labelled


def test_card_ask_untouched_without_drop(gate):
    plain = {k: v for k, v in DROPPED.items() if not k.startswith("resolved_check")}
    assert gate.core.card_ask({"human_request": plain}) == " ".join(ASK.split())[:160]


def test_unknown_or_missing_reason_still_warns(gate):
    out = gate.core.card_ask({"human_request": {"action": "x", "resolved_check_dropped_at": 5,
                                                "resolved_check_drop_reason": "something_new"}})
    assert "Automatic detection stopped" in out and "something_new" not in out


def test_l1_block_digest_warns_prominently(tmp_path, gate):
    out = gate.core.l1_block("chat", None, db_paths=[_db(tmp_path, DROPPED)])
    sec = out.split("Waiting on David now:")[1]
    assert "Automatic detection stopped" in sec and "manual unblock" in sec and "notice automatically" in sec
    assert sec.index("Automatic detection stopped") < sec.index("notice automatically")


def test_denied_wait_nudge_names_drop(gate):
    cards = [{"id": "t_00000000dropped", "status": "blocked", "human_request": DROPPED}]
    hit = gate.core.denied_wait("Nothing is waiting on you.", cards)
    assert hit and "Automatic detection stopped" in gate.core.card_ask(hit[1][0], 120)

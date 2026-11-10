"""self-heal agent-d9399023fc: in a long chat the live-state list kept only the 15 newest cards, so an older card
blocked on David fell off it and the agent told him nothing waited on him."""
import json
import sqlite3

ASK = ("Reply mini / macbook / none on this card: which host should run the repair. Unblock = run it there; "
       "archive = drop the repair.")


def _db(tmp_path, n_done=16, blocked=True, human=True):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("""create table tasks(id text, title text, status text, assignee text, created_at real,
        completed_at real, result text, session_id text, block_kind text, human_request text);
        create table kanban_notify_subs(task_id text, platform text, chat_id text);""")
    if blocked:
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
                  ("t_0000blockedc0de", "Pick the repair host", "blocked", "default", 100.0, None, "", "chat",
                   "needs_input", json.dumps({"action": ASK, "why_human": "only David knows"}) if human else None))
    for i in range(n_done):
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
                  (f"t_done{i:012x}", f"finished {i}", "done", "default", 1000.0 + i, 2000.0 + i, "", "chat",
                   None, None))
    c.commit()
    c.close()
    return db


def test_old_blocked_card_survives_many_newer_cards(tmp_path, gate):
    db = _db(tmp_path)
    ids = [x["id"] for x in gate.core.linked_cards("chat", db_paths=[db])]
    assert "t_0000blockedc0de" in ids


def test_l1_block_names_what_waits_on_david(tmp_path, gate):
    db = _db(tmp_path)
    out = gate.core.l1_block("chat", None, db_paths=[db])
    assert "t_0000blockedc0de [blocked" in out
    assert "Waiting on David now:" in out
    sec = out.split("Waiting on David now:")[1]
    assert "t_0000blockedc0de" in sec and "Reply mini / macbook / none" in sec
    assert out.index("Waiting on David now:") < out.index("Before saying anything waits on David")


def test_blocked_card_is_never_cut_even_past_the_cap(tmp_path, gate):
    db = tmp_path / "k.db"
    c = sqlite3.connect(db)
    c.execute("create table tasks(id text, title text, status text, assignee text, created_at real, "
              "completed_at real, result text, session_id text, block_kind text, human_request text)")
    for i in range(30):
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
                  (f"t_blk{i:012x}", "b", "blocked", "a", float(i), None, "", "chat", "needs_input",
                   json.dumps({"action": f"ask {i}"})))
    for i in range(30):
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
                  (f"t_new{i:012x}", "n", "done", "a", 1000.0 + i, 2000.0, "", "chat", None, None))
    c.commit(); c.close()
    ids = [x["id"] for x in gate.core.linked_cards("chat", db_paths=[db])]
    assert all(f"t_blk{i:012x}" in ids for i in range(30))


def test_finished_only_chat_is_unchanged(tmp_path, gate):
    db = _db(tmp_path, n_done=20, blocked=False)
    cards = gate.core.linked_cards("chat", db_paths=[db])
    assert [x["id"] for x in cards] == [f"t_done{i:012x}" for i in range(19, 4, -1)]  # the 15 newest, as before
    assert "Waiting on David" not in gate.core.l1_block("chat", None, db_paths=[db])


def test_legacy_board_without_human_request_columns_still_lists(board, gate, tmp_path):
    board("t_aaaaaaaa", "blocked", title="Other", created=1100.0)
    out = gate.core.l1_block("s1", None, db_paths=[tmp_path / "kanban.db"])
    assert "t_aaaaaaaa [blocked" in out and "Waiting on David now:" in out


# ---- detection: a "nothing waits on you" reply while a linked card is blocked on David ---------------------
def _wire(gate, monkeypatch, db):
    real = gate.core.linked_cards
    monkeypatch.setattr(gate.core, "linked_cards", lambda sid, limit=15, db_paths=None: real(sid, limit, [db]))


def test_reply_denying_a_wait_is_held_with_the_card(tmp_path, gate, monkeypatch):
    _wire(gate, monkeypatch, _db(tmp_path))
    r = gate.pre_final_gate(session_id="chat", platform="tui", attempt=0,
                            final_response="This repair is not waiting for your approval.")
    assert r and r.get("hold") and "t_0000blockedc0de" in r["nudge"]
    log = (tmp_path / "logs" / "ask-gate.jsonl")
    assert any(json.loads(l).get("kind") == "denied_wait_held" for l in log.read_text().splitlines())


def test_reply_denying_a_wait_passes_when_nothing_is_blocked(tmp_path, gate, monkeypatch):
    _wire(gate, monkeypatch, _db(tmp_path, blocked=False))
    assert gate.pre_final_gate(session_id="chat", platform="tui", attempt=0,
                               final_response="Nothing is waiting on you.") is None


def test_reply_denying_a_wait_released_after_budget(tmp_path, gate, monkeypatch):
    _wire(gate, monkeypatch, _db(tmp_path))
    assert gate.pre_final_gate(session_id="chat", platform="tui", attempt=2, hold_budget=2,
                               final_response="Nothing is waiting on you.") is None


def test_blocked_without_human_request_does_not_trigger_detection(tmp_path, gate, monkeypatch):
    _wire(gate, monkeypatch, _db(tmp_path, human=False))
    assert gate.pre_final_gate(session_id="chat", platform="tui", attempt=0,
                               final_response="Nothing is waiting on you.") is None

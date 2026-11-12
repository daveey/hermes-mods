"""claimed_mutation (self-heal agent-182d2d699c): a final reply that says the agent moved / archived / unblocked /
re-placed / reprioritized a card it names, when no board write of that card succeeded this turn or the turn before,
is nudged deterministically, before the judge.

Incident (hub session 20261112_002413_39be4c): msg 1202305 ended a hub-pin audit with 'The card is "Workers: recover
and alert when the user service manager dies", and I moved it to the pool.' The turn's bulk moves covered other ids;
no call named t_01a1cdb783d95a6a. Its first real move (msg 1202354, 33 min later) printed "kanban move:
t_01a1cdb783d95a6a is running: its worker moves it (kanban_move), or pass --reclaim [running]" from a command that
exited 0, and msg 1202355 then corrected the claim.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_cm_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")
ar = _mod("agent_rules")

CARD = "t_01a1cdb783d95a6a"
TITLE = "Workers: recover and alert when the user service manager dies"
INCIDENT_SENTENCE = (
    "- **Worker-1 red:** an open card says a test killed every process on the box at 02:34Z, and the box sat dead "
    "for 25 minutes. I saw the same outage again from 03:03Z to 03:14Z, when a login restarted it. The card is "
    f"\"{TITLE}\", and I moved it to the pool.")
INCIDENT_REPLY = (
    "**The hub queue is down from about 100 to 34. Almost none of them had to be on the hub: I moved 65 to the "
    "worker pool, and 8 of the 34 left have a real reason to stay.**\n\n" + INCIDENT_SENTENCE + "\n")
# msg 1202355's correction bullet, verbatim
CORRECTION_REPLY = (
    "- **Worker-1 service-manager card:** a correction — I told you earlier I had moved this to the pool, and I had "
    "not. It stayed on the hub and the hub has now started it.\n"
    "- **Fix card for cards other machines file for the hub:** it had sat ready on the full worker-1 for 10 minutes, "
    "so I moved it to worker-3, where it is running.")

OTHER = ["t_01a1cafd82625f0a", "t_01a1cdd4c509e623", "t_01a1cdd4c5bd193e"]


def _titles(title):
    return [CARD] if title == TITLE else []


def _bulk_moves_of_other_ids():
    """The turn's real shape: bulk `hermes kanban move` commands naming other cards, each with its MUT line."""
    log = []
    for cid in OTHER:
        args = {"command": f"cd ~; hermes kanban move {cid} --runs-on capability:hub-write --reason \"Portable\" "
                           f"2>&1 | tail -1"}
        res = json.dumps({"output": f"Moved {cid}", "exit_code": 0, "error": None})
        log.append(f"-> terminal({args['command'][:140]})")
        log.append(ar.mutation_result_line("terminal", args, res))
    return log


def _no_judge(system, prompt):
    raise AssertionError("claimed_mutation must not need the LLM judge")


def test_incident_reply_naming_the_card_by_title_is_flagged_when_only_other_ids_moved():
    hit = rc.claimed_mutation(INCIDENT_REPLY, _bulk_moves_of_other_ids(), card_titles=_titles)
    assert hit and hit["card_id"] == CARD and hit["verb"] == "moved"
    assert "I moved it to the pool" in hit["evidence"]


def test_incident_reply_through_check_is_a_deterministic_hit_with_the_nudge():
    hit = rc.check(INCIDENT_REPLY, "sweep the hub queue", _bulk_moves_of_other_ids(), _no_judge,
                   only=["claimed_mutation"], card_titles=_titles)
    assert hit and hit["rule"] == "claimed_mutation" and hit["p"] == 1.0 and hit["card"] == CARD
    assert "you moved card" in hit["nudge"] and f"hermes kanban show {CARD}" in hit["nudge"]
    assert "correct the claim" in hit["nudge"]


def test_claimed_mutation_is_a_default_and_outcome_rule():
    assert "claimed_mutation" in rc.RULES and "claimed_mutation" in rc.DEFAULT_RULES
    assert set(rc.RULES) == set(_mod("signals").OUTCOME_RULES) == set(_mod("self_heal").OUTCOME_RULES)


def test_a_move_that_really_ran_and_printed_moved_does_not_flag():
    args = {"command": f"hermes kanban move {CARD} --runs-on capability:hub-write"}
    res = json.dumps({"output": f"Moved {CARD}\n", "exit_code": 0, "error": None})
    mut = ar.mutation_result_line("terminal", args, res)
    assert mut == f"   <- MUT ok={CARD}"
    log = _bulk_moves_of_other_ids() + [f"-> terminal({args['command']})", mut]
    assert rc.claimed_mutation(INCIDENT_REPLY, log, card_titles=_titles) is None
    # by id, and a short id, too
    assert rc.claimed_mutation(f"I moved `{CARD}` to the pool.", log) is None
    assert rc.claimed_mutation(f"I moved {CARD[:12]} to the pool.", log) is None


def test_a_refused_move_with_exit_0_flags():
    args = {"command": f"cd ~; hermes kanban move {CARD} --runs-on capability:hub-write 2>&1 | tail -1; "
                       f"sqlite3 ~/.hermes/kanban.db \"select id,status from tasks where id='{CARD}'\""}
    res = json.dumps({"output": f"kanban move: {CARD} is running: its worker moves it (kanban_move), or pass "
                                f"--reclaim [running]\n04:07:55\n{CARD} | running | hub", "exit_code": 0,
                      "error": None})
    mut = ar.mutation_result_line("terminal", args, res)
    assert mut == f"   <- MUT ok= refused={CARD}"
    log = [f"-> terminal({args['command'][:140]})", mut]
    hit = rc.claimed_mutation(INCIDENT_REPLY, log, card_titles=_titles)
    assert hit and hit["card_id"] == CARD


def test_a_failed_move_without_a_mut_line_flags():
    log = [f"-> terminal(hermes kanban move {CARD} --runs-on capability:hub-write)",
           "   <- ERROR {\"output\": \"no such card\", \"exit_code\": 1}"]
    assert rc.claimed_mutation(f"I moved {CARD} to the pool.", log)


def test_a_kanban_tool_write_counts_and_its_refusal_does_not():
    ok = '{"ok": true, "task_id": "%s", "error": null}' % CARD
    mut = ar.mutation_result_line("kanban_move", {"task_id": CARD, "runs_on": ["capability:hub-write"]}, ok)
    assert mut == f"   <- MUT ok={CARD}"
    log = [f"-> kanban_move({{\"task_id\": \"{CARD}\"}})", mut]
    assert rc.claimed_mutation(f"I moved {CARD} to the pool.", log) is None
    bad = '{"ok": false, "error": "foreign_card: %s lives on another gateway"}' % CARD
    mut = ar.mutation_result_line("kanban_move", {"task_id": CARD}, bad)
    assert mut == f"   <- MUT ok= refused={CARD}"
    assert rc.claimed_mutation(f"I moved {CARD} to the pool.", [log[0], mut])
    # a deferred tool_call write
    calls = {"calls": [{"name": "kanban_supervisor_request",
                        "arguments": {"ops": [{"op": "operator", "action": "move", "task_id": CARD}]}}]}
    mut = ar.mutation_result_line("tool_call", calls, '{"results": [{"ok": true}]}')
    assert mut == f"   <- MUT ok={CARD}"
    # reads and comments are not writes
    assert ar.mutation_result_line("kanban_show", {"task_id": CARD}, "{}") is None
    assert ar.mutation_result_line("kanban_comment", {"task_id": CARD, "body": "x"}, "{}") is None
    assert ar.mutation_result_line("terminal", {"command": f"hermes kanban show {CARD}"}, "{}") is None


def test_a_card_this_turn_created_counts_as_written():
    new = "t_01a1cfdf01988a98"
    mut = ar.mutation_result_line("kanban_create", {"title": "x", "parents": [CARD]},
                                  '{"ok": true, "task_id": "%s", "error": null}' % new)
    assert mut == f"   <- MUT ok={new}"
    reply = f"Replaced the stuck card. I archived it and opened `{new}` for the two checks still left."
    assert rc.claimed_mutation(reply, ["-> kanban_create({\"title\": \"x\"})", mut]) is None
    cli = ar.mutation_result_line("terminal", {"command": "hermes kanban create 'x' --assignee default"},
                                  json.dumps({"output": f"Created {new}", "exit_code": 0}))
    assert cli == f"   <- MUT ok={new}"
    assert ar.mutation_result_line("kanban_create", {"title": "x"}, '{"ok": false, "error": "create_overlap"}') is None


def test_backtest_shapes_hub_2026_11():
    """False hits from a 7-day hub backtest (2026-11-12) that must pass."""
    # an edit run through subprocess with an argv list
    code = ("import subprocess; subprocess.check_output(['hermes','kanban','edit','t_01a19400313cfbda',"
            "'--priority','50002'])")
    mut = ar.mutation_result_line("execute_code", {"code": code}, json.dumps({"output": "", "exit_code": 0}))
    assert mut == "   <- MUT ok=t_01a19400313cfbda"
    # archive + create in one deferred tool_call: the new card counts, the create's body ids don't
    calls = {"calls": [{"name": "kanban_archive", "arguments": {"task_id": "t_01a1c7e5651f2ba3"}},
                       {"name": "kanban_create", "arguments": {"title": "x", "body": "was t_01a1c7e44f88146c"}}]}
    res = '{"results": [{"ok": true}, {"ok": true, "task_id": "t_01a1c7e93be80bfb"}]}'
    assert ar.mutation_result_line("tool_call", calls, res) == "   <- MUT ok=t_01a1c7e5651f2ba3,t_01a1c7e93be80bfb"
    # a successful edit followed by a read whose unrelated text says "cannot"
    cmd = "hermes kanban edit t_01a1ceef028317c0 --priority 900 >/dev/null; hermes kanban show t_01a1ceef028317c0"
    out = json.dumps({"output": "queue:     waiting for worker-2; this machine cannot lease it.", "exit_code": 0})
    assert ar.mutation_result_line("terminal", {"command": cmd}, out) == "   <- MUT ok=t_01a1ceef028317c0"
    # but a generic CLI refusal line still counts for a one-card call
    out = json.dumps({"output": "kanban edit: refused: card is archived", "exit_code": 0})
    assert ar.mutation_result_line("terminal", {"command": cmd}, out) == "   <- MUT ok= refused=t_01a1ceef028317c0"


def test_a_write_in_the_previous_turn_counts():
    prev = [f"-> terminal(hermes kanban edit {CARD} --priority 5)", f"   <- MUT ok={CARD}"]
    assert rc.claimed_mutation(f"I raised the priority of {CARD} to 5.", [], prev_log=prev) is None
    hit = rc.check(f"I raised the priority of {CARD} to 5.", "status?", [], _no_judge, only=["claimed_mutation"],
                   prev_turn={"rich": prev, "reply": "", "gap_s": 10})
    assert hit is None
    assert rc.claimed_mutation(f"I raised the priority of {CARD} to 5.", [], prev_log=[])


def test_third_person_future_tense_negated_and_correction_mentions_do_not_flag():
    for reply in [
        f"The worker moved {CARD} to the pool.",
        f"{CARD} was moved to the pool by its owner.",
        f"I'll move {CARD} to the pool once worker-1 frees a slot.",
        f"I will archive {CARD} after the proof passes.",
        f"Next I move {CARD} to the pool.",
        f"I haven't moved {CARD} yet: its worker moves it.",
        f"I did not unblock {CARD}.",
        f"Want me to move {CARD} to the pool?",
        f"> I moved {CARD} to the pool.",
        f"```\nI moved {CARD} to the pool.\n```",
        f"I raised the issue on {CARD} in a comment.",
        f"I moved 65 cards to the worker pool.",
        f"The card is \"{TITLE}\", and the hub started it.",
        CORRECTION_REPLY,
    ]:
        assert rc.claimed_mutation(reply, [], card_titles=_titles) is None, reply


def test_unknown_title_or_board_error_fails_open():
    two = ["t_01a1cdcc00000001", "t_01a1cdcc00000002"]
    reply2 = "The card is \"Shared title of two cards\", and I archived it."
    assert rc.claimed_mutation(reply2, [f"-> terminal(hermes kanban archive {two[1]})", f"   <- MUT ok={two[1]}"],
                               card_titles=lambda t: two) is None
    assert rc.claimed_mutation(reply2, [], card_titles=lambda t: two)["card_id"] == two[0]
    reply = "The card is \"Some card no board has\", and I moved it to the pool."
    assert rc.claimed_mutation(reply, [], card_titles=lambda t: []) is None

    def boom(title):
        raise sqlite3.OperationalError("database is locked")
    assert rc.claimed_mutation(reply, [], card_titles=boom) is None
    assert rc.check(reply, "x", [], _no_judge, only=["claimed_mutation"], card_titles=boom) is None


def test_card_ids_for_title_reads_the_board(tmp_path):
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute("create table tasks (id text primary key, title text, status text)")
    conn.execute("insert into tasks values (?, ?, 'running')", (CARD, TITLE + " (worker-1 sat dead 25 min)"))
    conn.execute("insert into tasks values ('t_01a1cdcc00000001', 'Other card', 'ready')")
    conn.execute("insert into tasks values ('t_01a1cdcc00000002', 'Workers: recover and alert when the user "
                 "service manager dies soon', 'ready')")
    conn.commit()
    conn.close()
    # the real hub title carries a parenthetical the reply dropped
    assert rc.card_ids_for_title(TITLE, dbs=[str(db)]) == [CARD]
    assert rc.card_ids_for_title(TITLE.lower() + ".", dbs=[str(db)]) == [CARD]
    assert rc.card_ids_for_title(TITLE + " (worker-1 sat dead 25 min)", dbs=[str(db)]) == [CARD]
    assert rc.card_ids_for_title("Other card", dbs=[str(db)]) == ["t_01a1cdcc00000001"]
    assert rc.card_ids_for_title("Other", dbs=[str(db)]) == []
    assert rc.card_ids_for_title("No such title", dbs=[str(db)]) == []
    assert rc.card_ids_for_title("100%_", dbs=[str(db)]) == []
    assert rc.card_ids_for_title(TITLE, dbs=[str(tmp_path / "missing.db")]) == []
    hit = rc.claimed_mutation(INCIDENT_REPLY, _bulk_moves_of_other_ids(),
                              card_titles=lambda t: rc.card_ids_for_title(t, dbs=[str(db)]))
    assert hit and hit["card_id"] == CARD


def test_observe_writes_the_mut_line_into_both_turn_logs():
    coach = ar.AgentCoach()
    sid = "s-claimed-mutation"
    coach.observe(sid, "terminal", {"command": f"hermes kanban move {CARD} --runs-on capability:hub-write"},
                  json.dumps({"output": f"kanban move: {CARD} is running: its worker moves it", "exit_code": 0}))
    _, log = coach.turn_context(sid)
    assert f"   <- MUT ok= refused={CARD}" in log
    assert f"   <- MUT ok= refused={CARD}" in coach.turn_rich(sid)

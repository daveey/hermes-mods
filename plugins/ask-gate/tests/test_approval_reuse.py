"""An approval David gave stays usable until something ran under it (agent-159f5f5152).

Incident (scrubbed): one chat asked David to approve the same priority reset five times in 40 h. Each run stopped
before any write on a fixable refusal (rotated python path, stale snapshot), its receipt said not_applied, and the
agent published a fresh approval for the same machines and data, with only a new map hash and checkout. Its
``attempts`` limit said "No rerun without asking you." The judge passed every repeat as a new decision. David:
"i already approved this a bunch of times, why do youkeep asking me".
"""
import json
import sqlite3

from conftest import llm_returning

GOAL = "t_01a1cc1e1ffbe650"
MACHINES = ("hub, laptop, mini, worker-1, worker-2, worker-3, worker-4, worker-5, worker-6, worker-7, worker-8: the "
            "11 writers in host-map-{run}.json (sha256 {sha})")
DATA = ("Kanban card priority values only, rescaled to 1-900 keeping relative order, via the migrate command in "
        "checkout {checkout} (it takes its own fresh snapshot and per-machine backups)")
SINGLE = "one run; on any refusal or failure the tool's own guarded restore runs, then I stop and report. No rerun without asking you."
RETRY = ("one run. A refusal before anything changes doesn't use this approval up: I fix the cause and rerun under "
         "it. After a failure that changed something, the guarded restore runs and I stop and report.")


def _req(title, *, run, sha, checkout, card, attempts=RETRY, markdown="Plan.", machines=None):
    return {"schemaVersion": 2, "kind": "approval", "title": title, "state": "open", "aliases": [],
            "scope": {"markdown": markdown, "complete": True,
                      "sourceRefs": [{"type": "kanban", "id": card, "board": "default"},
                                     {"type": "kanban", "id": GOAL, "board": "default"}]},
            "limits": [{"key": "machines", "permissionRequired": True,
                        "value": machines or MACHINES.format(run=run, sha=sha)},
                       {"key": "data changed", "permissionRequired": True, "value": DATA.format(checkout=checkout)},
                       {"key": "attempts", "value": attempts}],
            "exclusions": ["No change to card status, assignees, titles or history: priority numbers only"],
            "outcomes": {"approve": "I run the recheck, then the command once.", "decline": "Nothing runs."},
            "choices": [{"id": "approve", "label": "Run it on all 11 machines", "effect": "approve"},
                        {"id": "revise", "label": "Change the plan", "effect": "revise"},
                        {"id": "decline", "label": "Don't run it", "effect": "decline"}]}


RUN3 = _req("Run the priority reset (run 3) on all 11 machines that can own cards, with the snapshot race fixes; "
            "auto-undo on failure", run="run3c", sha="0a5a0c", checkout="1273771b42", card="t_01a1d40af9e31925",
            attempts=SINGLE)
RUN3D = _req("Run the priority reset (run 3) on all 11 machines, now immune to Hermes updates; auto-undo on failure",
             run="run3d", sha="bd46b6", checkout="dc671254d6", card="t_01a1d5fb97d508ed",
             markdown="**Why you're seeing this again.** You approved run 3 at 17:35Z. I stopped before touching "
                      "anything: the plan named an exact python path and Hermes updates replaced it.")
OP = "37946c38-70f4-448a-8087-ae31ad6a4dc3"


def _decision(req, op=OP):
    return ("Canonical user decision. Act only on the frozen revision below. Approve permits only this exact scope "
            "and limits.\n" + json.dumps({"effect": "approve", "executionAuthorized": True, "operationId": op,
                                           "permissionGrants": [], "request": req, "revisionText": None})
            + "\nOwner receipt: after validating, call action_request {\"receipt\": ...}")


def _receipt(phase, op=OP):
    return json.dumps({"receipt": {"operationId": op, "requestId": "b9f075df", "revision": 1, "phase": phase}})


def _chat(tmp_path, rows, session="s1"):
    c = sqlite3.connect(tmp_path / "state.db")
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
              "display_kind text, active integer default 1, compacted integer default 0, message_uid text, "
              "timestamp real)")
    for i, (role, text) in enumerate(rows):
        c.execute("insert into messages (session_id, role, content, message_uid, timestamp) values (?,?,?,?,?)",
                  (session, role, text, f"m{i}", 1794590000 + i))
    c.commit()
    c.close()


def _publish(gate, req, session="s1"):
    gate.LLM = llm_returning([("ok", "not a repeat of any answered ask in the ledger")])
    return gate.pre_tool_call(tool_name="action_request", session_id=session,
                              args={"decisionCard": {"board": "default"}, "request": req})


def test_repeat_of_unused_approval_is_held(gate, tmp_path):
    """The live round: run 3 approved, stopped before any write (not_applied), run 3d re-asks the same scope."""
    _chat(tmp_path, [("user", "reset the priorities"), ("user", _decision(RUN3)),
                     ("tool", _receipt("acknowledged")), ("tool", _receipt("not_applied"))])
    r = _publish(gate, RUN3D)
    assert r and r["action"] == "block", r
    assert "approval_reuse" in r["message"] and "37946c38" in r["message"]
    assert "New since your approval:" in r["message"]


def test_single_use_attempts_clause_is_held(gate, tmp_path):
    """The root: a request that makes its own approval single-use turns every pre-write refusal into a new ask."""
    _chat(tmp_path, [("user", "reset the priorities")])
    r = _publish(gate, RUN3)
    assert r and r["action"] == "block", r
    assert "approval_single_use" in r["message"] and "No rerun without asking" in r["message"]


def test_retry_terms_first_approval_publishes(gate, tmp_path):
    _chat(tmp_path, [("user", "reset the priorities")])
    assert _publish(gate, RUN3D) is None


def test_approval_that_ran_is_a_new_decision(gate, tmp_path):
    """Something ran under the earlier approval (reserved/applied): a second run needs David again."""
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("acknowledged")), ("tool", _receipt("reserved")),
                     ("tool", _receipt("applied"))])
    assert _publish(gate, RUN3D) is None


def test_declared_permission_widening_publishes(gate, tmp_path):
    wider = json.loads(json.dumps(RUN3D))
    wider["scope"]["markdown"] += "\n\nNew since your approval: data changed: also resets the done cards."
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("not_applied"))])
    assert _publish(gate, wider) is None


def test_added_target_publishes(gate, tmp_path):
    """More machines than he approved is a new decision even without the declaration line."""
    wider = json.loads(json.dumps(RUN3D))
    wider["limits"][0]["value"] = wider["limits"][0]["value"].replace("worker-8:", "worker-8, worker-9:")
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("not_applied"))])
    assert _publish(gate, wider) is None


def test_undeclared_wording_only_line_does_not_release(gate, tmp_path):
    """A 'New since' line that names no permission limit (only a new launcher path) is not a widening."""
    same = json.loads(json.dumps(RUN3D))
    same["scope"]["markdown"] += "\n\nNew since your approval: a launcher picks the current python."
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("not_applied"))])
    r = _publish(gate, same)
    assert r and "approval_reuse" in r["message"], r


def test_new_permission_limit_key_publishes(gate, tmp_path):
    more = json.loads(json.dumps(RUN3D))
    more["limits"].append({"key": "spend", "permissionRequired": True, "value": "up to $40 of GPU time"})
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("not_applied"))])
    assert _publish(gate, more) is None


def test_unrelated_approval_under_same_goal_publishes(gate, tmp_path):
    other = _req("Delete the stale backup files on the worker fleet", run="x", sha="ff", checkout="aa",
                 card="t_01a1d9000000aaaa",
                 machines="worker-1, worker-2: backup directories older than 30 days under ~/.hermes/backups")
    other["limits"][1]["value"] = "backup files only"
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("not_applied"))])
    assert _publish(gate, other) is None


def test_other_chat_approval_does_not_cover(gate, tmp_path):
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("not_applied"))], session="other")
    assert _publish(gate, RUN3D, session="s1") is None


def test_hold_is_bounded(gate, tmp_path):
    _chat(tmp_path, [("user", _decision(RUN3)), ("tool", _receipt("not_applied"))])
    held = [_publish(gate, RUN3D) for _ in range(3)]
    assert [bool(h) for h in held] == [True, True, False]

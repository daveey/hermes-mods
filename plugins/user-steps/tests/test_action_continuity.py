"""Source-owned creation-boundary continuity, using isolated synthetic ledgers."""
import copy
import importlib.util
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from contextlib import contextmanager
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("continuity_core", Path(__file__).resolve().parents[1] / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
ASK = "Want me to deploy build 731 to staging?"
ACTION = {"text": "Approve deploying build 731 to staging", "kind": "approve",
          "verbatim_span": ASK, "options": []}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return core.Ledger(tmp_path / "steps", tmp_path / "missing.db")


def run(ledger, actions=None, reply=ASK, root="chat", resolved=(), **kwargs):
    payload = {"steps": copy.deepcopy(actions if actions is not None else [ACTION]), "resolved": list(resolved)}
    return core.process_reply(reply, root, ledger=ledger, llm=lambda *_: json.dumps(payload), **kwargs)


def seed(ledger, status="resolved", action=None, root="chat", ident="us_abcdef1234"):
    step = {**copy.deepcopy(action or ACTION), "id": ident, "status": status,
            "created": 1, "session": root, "paragraph": ASK}
    with ledger.edit(root) as d:
        d["steps"].append(step)
    return step


def test_open_exact_action_reuses_source_id(ledger):
    first = run(ledger)
    second = run(ledger, reply="Checks passed.\n\n" + ASK)
    assert second["steps"][0]["id"] == first["steps"][0]["id"]
    assert len(ledger.read("chat")["steps"]) == 1
    assert first["steps"][0]["id"] in second["text"]


@pytest.mark.parametrize("framed", [True, False])
def test_resolved_action_removed_before_any_new_id_card_or_frame(ledger, monkeypatch, framed):
    old = seed(ledger)
    monkeypatch.setattr(core, "new_step_id", lambda: pytest.fail("retired action allocated an ID"))
    monkeypatch.setattr(core, "sync_card", lambda *a: pytest.fail("retired action triggered card sync"))
    monkeypatch.setattr(core, "frame", lambda *a: pytest.fail("retired action reached framing"))
    out = run(ledger, reply="Checks passed.\n\n" + ASK, framed=framed)
    assert out["steps"] == [] and out["text"] == core.HELD_REPLY
    assert ledger.read("chat")["steps"] == [old]


def test_same_reply_resolution_precedes_repeat_creation(ledger):
    old = seed(ledger, "open")
    out = run(ledger, resolved=[old["id"]])
    assert out["resolved"] == [old["id"]]
    assert out["steps"] == [] and ASK not in out["text"]
    assert len(ledger.read("chat")["steps"]) == 1


def test_reconciliation_uses_locked_current_state_not_pre_extract_snapshot(ledger, monkeypatch):
    old = seed(ledger, "open")
    edit = ledger.edit
    fired = False

    @contextmanager
    def racing_edit(root):
        nonlocal fired
        if not fired:
            fired = True
            with edit(root) as d:
                d["steps"][0]["status"] = "resolved"
        with edit(root) as d:
            yield d

    monkeypatch.setattr(ledger, "edit", racing_edit)
    out = run(ledger)
    assert fired and out["steps"] == [] and ASK not in out["text"]
    assert ledger.read_exact("chat", old["id"])["step"]["status"] == "resolved"


def test_concurrent_creation_is_reused_under_lock(ledger, monkeypatch):
    edit = ledger.edit
    fired = False

    @contextmanager
    def racing_edit(root):
        nonlocal fired
        if not fired:
            fired = True
            with edit(root) as d:
                d["steps"].append({**ACTION, "id": "us_aabbcc", "status": "open", "created": 1})
        with edit(root) as d:
            yield d

    monkeypatch.setattr(ledger, "edit", racing_edit)
    out = run(ledger)
    assert out["steps"][0]["id"] == "us_aabbcc"
    assert len(ledger.read("chat")["steps"]) == 1


@pytest.mark.parametrize("field,value", [("text", "Approve a different build"), ("kind", "input"),
                                        ("verbatim_span", "Want me to deploy build 732 to staging?")])
def test_identity_is_exact_not_topic_or_account_matching(ledger, field, value):
    seed(ledger)
    action = {**ACTION, field: value}
    out = run(ledger, [action], reply=action["verbatim_span"])
    assert len(out["steps"]) == 1 and len(ledger.read("chat")["steps"]) == 2


def test_options_are_part_of_exact_identity(ledger):
    action = {**ACTION, "kind": "choose", "options": ["Yes", "No"]}
    seed(ledger, action=action)
    out = run(ledger, [{**action, "options": ["No", "Yes"]}])
    assert len(out["steps"]) == 1


def test_root_and_profile_isolation(ledger, tmp_path):
    seed(ledger)
    assert len(run(ledger, root="other")["steps"]) == 1
    other = core.Ledger(tmp_path / "other-profile", tmp_path / "missing.db")
    assert len(run(other)["steps"]) == 1


def test_model_reopen_fields_are_not_authority(ledger):
    seed(ledger)
    out = run(ledger, [{**ACTION, "reopen": True, "status": "open", "resolved_by": "user",
                        "id": "us_ffffff", "verified": True}])
    assert out["steps"] == [] and ASK not in out["text"]
    assert ledger.read("chat")["steps"][0]["status"] == "resolved"


def test_nonexact_location_holds_entire_reply_and_does_not_publish_other_actions(ledger):
    seed(ledger)
    other = {"text": "Approve launch", "kind": "approve", "verbatim_span": "Want me to launch it?"}
    out = run(ledger, [ACTION, other], reply=ASK.replace("deploy", "**deploy**") + "\n\n" + other["verbatim_span"])
    assert out["steps"] == []
    assert out["text"] == core.HELD_REPLY
    assert len(ledger.read("chat")["steps"]) == 1


def test_retired_ask_holds_whole_draft_including_other_actions(ledger):
    seed(ledger)
    other = {"text": "Approve launch", "kind": "approve", "verbatim_span": "Want me to launch it?"}
    out = run(ledger, [ACTION, other], reply=ASK + "\n\n" + ASK + "\n\n" + other["verbatim_span"])
    assert out["text"] == core.HELD_REPLY and out["steps"] == []
    assert len(ledger.read("chat")["steps"]) == 1


def test_new_action_in_removed_shared_block_is_not_persisted(ledger):
    seed(ledger)
    other = {"text": "Approve launch", "kind": "approve", "verbatim_span": "Want me to launch it?"}
    out = run(ledger, [ACTION, other], reply=ASK + " " + other["verbatim_span"])
    assert out["steps"] == [] and out["text"] == core.HELD_REPLY
    assert len(ledger.read("chat")["steps"]) == 1


def test_retirement_and_open_reuse_are_not_bypassed_by_hourly_limit(ledger):
    seed(ledger)
    with ledger.edit("chat") as d:
        d["steps"].extend({**ACTION, "text": str(i), "id": f"us_{i:06x}", "created": 100,
                           "status": "open"} for i in range(core.MAX_NEW_PER_HOUR))
    out = run(ledger, now=101)
    assert out["steps"] == [] and ASK not in out["text"]


def test_compression_descendant_uses_same_retirement(ledger):
    with sqlite3.connect(ledger.db) as db:
        db.execute("create table sessions (id text, parent_session_id text, end_reason text, source text)")
        db.executemany("insert into sessions values (?, ?, ?, ?)",
                       [("chat", None, "compression", "desktop"), ("tip", "chat", None, "desktop")])
    seed(ledger)
    out = run(ledger, root="tip")
    assert out["steps"] == [] and ASK not in out["text"]
    assert not ledger.path("tip").exists()


def test_resolution_from_another_writer_during_extraction(ledger):
    old = seed(ledger, "open")
    entered, released = Event(), Event()

    def llm(*_):
        entered.set()
        assert released.wait(5)
        return json.dumps({"steps": [ACTION]})

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(core.process_reply, ASK, "chat", ledger=ledger, llm=llm)
        try:
            assert entered.wait(5)
            other_writer = core.Ledger(ledger.dir, ledger.db)
            assert core.resolve(other_writer, "chat", [old["id"]], "declined", "desktop") == [old["id"]]
        finally:
            released.set()
        out = pending.result(timeout=5)
    assert out["steps"] == [] and ASK not in out["text"]
    assert len(ledger.read("chat")["steps"]) == 1


def test_retired_sixth_extraction_not_hidden_by_creation_budget(ledger):
    seed(ledger)
    actions = [{"text": f"Approve launch {i}", "kind": "approve",
                "verbatim_span": f"Want me to launch build {i}?"} for i in range(5)]
    out = run(ledger, actions + [ACTION], reply="\n\n".join(s["verbatim_span"] for s in actions + [ACTION]))
    assert out["steps"] == [] and out["text"] == core.HELD_REPLY
    assert len(ledger.read("chat")["steps"]) == 1


def test_open_reuse_does_not_consume_creation_budget(ledger):
    old = seed(ledger, "open")
    out = run(ledger, now=2)
    assert [s["id"] for s in out["steps"]] == [old["id"]]
    with ledger.edit("chat") as d:
        d["steps"].extend({**ACTION, "text": str(i), "id": f"us_{i:06x}", "created": 1,
                           "status": "open"} for i in range(core.MAX_NEW_PER_HOUR))
    out = run(ledger, now=2)
    assert [s["id"] for s in out["steps"]] == [old["id"]]


def test_existing_label_dedup_for_nonexact_action_is_preserved(ledger):
    seed(ledger, "open")
    out = run(ledger, [{**ACTION, "verbatim_span": "Want me to launch it?"}], reply="Want me to launch it?")
    assert out["steps"] == [] and out["text"] is None
    assert len(ledger.read("chat")["steps"]) == 1


@pytest.mark.parametrize("failure", ["write", "cards", "none"])
def test_transform_never_resurrects_retired_request(ledger, monkeypatch, failure):
    spec = importlib.util.spec_from_file_location("continuity_plugin", Path(__file__).resolve().parents[1] / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    seed(ledger, "open" if failure == "cards" else "resolved")
    monkeypatch.setattr(plugin, "core", core)
    monkeypatch.setattr(plugin, "_eligible", lambda *a: True)
    monkeypatch.setattr(plugin, "_ledger", lambda: ledger)
    monkeypatch.setattr(plugin, "_framed", lambda *a: False)
    monkeypatch.setattr(plugin, "_trace", lambda **kw: None)
    monkeypatch.setattr(plugin, "aux_llm", lambda *a: json.dumps({"steps": [ACTION], "resolved": ["us_abcdef1234"]}))
    def fail(*a, **kw):
        raise OSError("synthetic publication failure")
    if failure == "write":
        monkeypatch.setattr(core.os, "replace", fail)
    if failure == "cards":
        monkeypatch.setattr(core, "sync_card", fail)
    reply = ASK + "\n\nReply yes to approve deploying build 731 to staging, or no to decline."
    assert plugin.transform_llm_output(reply, "chat", "desktop") == core.HELD_REPLY


def test_regex_repeat_is_suppressed(ledger):
    first = core.process_reply(ASK, "chat", ledger=ledger)
    core.resolve(ledger, "chat", [first["steps"][0]["id"]], "declined", "desktop")
    out = core.process_reply(ASK, "chat", ledger=ledger)
    assert out["steps"] == [] and ASK not in out["text"]

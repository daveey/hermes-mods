"""Physical identity is evidence-specific; prerequisites never constitute consent."""
import json
import sqlite3

import pytest
from conftest import llm_returning


def human(**changes):
    return {"action": "Record one pickup on the test phone. Unblock = inspect; archive = abandon.",
            "why_human": "Only a person can perform the pickup",
            "scope": "DEVICE: test-phone\nEVIDENCE: pickup-session-A",
            "evidence": "terminal inspected capture inventory", "interaction": "physical", **changes}


def invoke(gate, request, tool="kanban_create", **extra):
    gate.LOG.add("s1", "terminal", "capture inventory", "capture absent")
    gate.LLM = llm_returning([("ok", "physical action")])
    return gate.pre_tool_call(tool_name=tool, session_id="s1", args={
        "initial_status": "blocked", "kind": "needs_input", "human_request": request, **extra})


def seed(tmp_path, request, status="blocked"):
    db = tmp_path / "kanban" / "boards" / "devices" / "kanban.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as c:
        c.execute("create table tasks (id text, title text, status text, human_request text)")
        c.execute("insert into tasks values (?, ?, ?, ?)",
                  ("t_1234abcd", "Pickup evidence", status, json.dumps(request)))
    return db


@pytest.mark.parametrize("interaction", ["physical", " physical "])
@pytest.mark.parametrize("tool", ["kanban_create", "kanban_block"])
def test_second_same_act_redirects_to_first_card_on_other_board(gate, tmp_path, tool, interaction):
    db = seed(tmp_path, human())
    result = invoke(gate, human(action="Please capture the pickup once.", interaction=interaction), tool)
    assert result and result["action"] == "block"
    assert "physical_ask_duplicate" in result["message"]
    assert "t_1234abcd" in result["message"] and "devices" in result["message"]
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from tasks").fetchone()[0] == 1
        assert c.execute("select status from tasks").fetchone()[0] == "blocked"


@pytest.mark.parametrize("interaction", ["physical", " physical "])
@pytest.mark.parametrize("prerequisite", ["PREREQUISITE: installed build 42", "PREREQUISITE: file /captures/pickup.wav",
                                         "PREREQUISITE: waiter job login-42", "EXPIRES: 2030-01-01T00:00:00Z"])
def test_readable_prerequisite_cannot_file_stale_unprobed_ask(gate, prerequisite, interaction):
    result = invoke(gate, human(scope=human()["scope"] + "\n" + prerequisite, interaction=interaction))
    assert result and "physical_ask_probe_required" in result["message"]


@pytest.mark.parametrize("scope", ["DEVICE: other-phone\nEVIDENCE: pickup-session-A",
                                   "DEVICE: test-phone\nEVIDENCE: pickup-session-B"])
def test_different_device_or_evidence_is_not_deduplicated(gate, tmp_path, scope):
    seed(tmp_path, human())
    assert invoke(gate, human(scope=scope)) is None


@pytest.mark.parametrize("status", ["done", "archived", "cancelled"])
def test_closed_ask_is_not_an_owner(gate, tmp_path, status):
    seed(tmp_path, human(), status)
    assert invoke(gate, human()) is None


def test_owner_can_refresh_its_own_ask(gate, tmp_path):
    seed(tmp_path, human())
    assert invoke(gate, human(), "kanban_block", task_id="t_1234abcd") is None


def test_probe_is_left_to_core_not_executed_by_plugin(gate):
    assert invoke(gate, human(scope=human()["scope"] + "\nPREREQUISITE: installed build 42",
                              resolved_check="exit 0")) is None


def test_already_met_prerequisite_never_reaches_blocked_write(gate, tmp_path):
    # Exercise the real core pre-persistence check, not a fake probe return.
    resolved = pytest.importorskip("hermes_cli.kanban_ask_resolved")
    check_create_filing, AskFilingRefused = resolved.check_create_filing, resolved.AskFilingRefused
    ready = tmp_path / "installed-build"
    ready.write_text("42")
    request = human(scope=human()["scope"] + "\nPREREQUISITE: installed build 42")
    # Without a probe core admits this stale request; the new hook must refuse it.
    check_create_filing(request, "blocked")
    assert invoke(gate, request) is not None
    request["resolved_check"] = "test -f " + str(ready)
    assert invoke(gate, request) is None  # defer execution to the target-context kernel
    with pytest.raises(AskFilingRefused) as caught:
        check_create_filing(request, "blocked", context={"cwd": str(tmp_path)})
    assert caught.value.code == "ask_already_resolved"


def test_profile_asks_are_discovered(gate, tmp_path, monkeypatch):
    old = seed(tmp_path, human())
    target = tmp_path / "profiles" / "devices" / "kanban.db"
    target.parent.mkdir(parents=True)
    old.rename(target)
    # Current process is a different profile, not the root home.
    home = tmp_path / "profiles" / "worker"
    home.mkdir()
    monkeypatch.setattr(gate.core, "home", lambda: home)
    result = invoke(gate, human())
    assert result and "t_1234abcd" in result["message"]


def test_legacy_exact_request_is_deduplicated(gate, tmp_path):
    request = human(scope="one pickup recording on test-phone")
    seed(tmp_path, request)
    result = invoke(gate, request)
    assert result and "physical_ask_duplicate" in result["message"]


def test_signin_duplicate_is_not_new_consent(gate, tmp_path):
    request = human(interaction="external", action="Sign in on the test phone")
    seed(tmp_path, request)
    result = invoke(gate, request)
    assert result and "physical_ask_duplicate" in result["message"]

"""action=enter (agent-7e5ac22ea5): the user said in chat that a step is done while the job's window waits for
Enter; the agent presses Return in that exact window instead of asking the user to switch windows. No real GUI."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def ent(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("enter_contract_subject", Path(__file__).parents[1] / "__init__.py")
    ut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ut)
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ut, "_proc_start", lambda pid, **kw: "current-birth")
    monkeypatch.setattr(ut, "_tty_of", lambda pid: "/dev/ttys001")
    monkeypatch.setattr(ut, "_osascript_out", lambda script: pytest.fail("unexpected GUI call"))
    monkeypatch.setattr(ut, "relay_once", lambda *a, **kw: pytest.fail("unexpected courier publication"))
    job = ut.jobs_dir() / "canonical-job"
    job.mkdir(parents=True)
    (job / "status.json").write_text(json.dumps({"status": "running"}))
    (job / "runners").write_text("123 current-birth\n")
    (job / "meta.json").write_text(json.dumps({"origin": {"gateway": "origin-A"}}))
    (job / "output.log").write_text("Opening browser...\r\nSign in to Claude as you@example.com, then press Enter here. ")
    return ut, job


def test_schema_offers_enter_and_says_when(ent):
    ut, _ = ent
    assert "enter" in ut.SCHEMA["parameters"]["properties"]["action"]["enum"]
    assert "action=enter" in ut.SCHEMA["description"]
    assert "action=enter" in ut.REMOTE_SCHEMA["description"]


def test_pending_note_tells_the_agent_to_press_enter_itself(ent):
    ut, job = ent
    res = ut.wait_for(job, 0.1)
    assert res["status"] == "running"
    assert "action=enter" in res["note"]


def test_enter_presses_return_in_the_jobs_own_window(ent, monkeypatch):
    ut, job = ent
    scripts = []
    monkeypatch.setattr(ut, "_osascript_out", lambda script: scripts.append(script) or "iTerm")
    out = json.loads(ut.handle({"action": "enter", "job_id": job.name}))
    assert out["ok"] is True and out["pressed"] == "iTerm" and out["status"] == "running", out
    assert "press Enter" in out["prompt"]
    assert len(scripts) == 1 and '"/dev/ttys001"' in scripts[0]
    assert 'write text ""' in scripts[0] and 'do script "" in t' in scripts[0]  # Return only, never any text


def test_enter_refuses_when_the_window_does_not_ask_for_enter(ent):
    ut, job = ent
    (job / "output.log").write_text("Password: ")
    out = ut.press_enter(job)
    assert out["ok"] is False and "pressed" not in out and "Enter" in out["error"]


@pytest.mark.parametrize("line", ["enter your username:", "Return code 3", "enter passphrase:",
                                  "Enter the 6-digit code:", "Please enter a value (or return to skip):"])
def test_enter_refuses_lines_that_only_mention_enter_or_return(ent, monkeypatch, line):
    """Only an explicit 'press/hit/type Enter', 'Enter to continue' or '[Enter]' prompt is answered; a line that
    merely contains the word enter/return (a username/passphrase/code prompt, an exit-code report) is the
    user's to answer, so Return is never typed into it."""
    ut, job = ent
    calls = []
    monkeypatch.setattr(ut, "_osascript_out", lambda script: calls.append(script) or "iTerm")
    (job / "output.log").write_text(f"Starting...\r\n{line} ")
    out = ut.press_enter(job)
    assert out["ok"] is False and "pressed" not in out and calls == [], out
    assert "does not ask for Enter" in out["error"] and out["prompt"] == line


def test_enter_refuses_when_the_runner_pid_was_reused(ent, monkeypatch):
    """The recorded runner pid now belongs to a different process (its start time no longer matches), so its
    tty is not the job's window: nothing is pressed even though that pid has a live tty."""
    ut, job = ent
    calls, ttys_asked = [], []
    monkeypatch.setattr(ut, "_osascript_out", lambda script: calls.append(script) or "iTerm")
    monkeypatch.setattr(ut, "_tty_of", lambda pid: ttys_asked.append(pid) or "/dev/ttys009")
    (job / "runners").write_text("123 earlier-birth\n")
    out = ut.press_enter(job)
    assert out["ok"] is False and "pressed" not in out and calls == [], out
    assert "no live window" in out["error"]
    assert ttys_asked == []  # the reused pid's tty is never even looked up


def test_enter_presses_only_the_runner_that_is_still_the_same_process(ent, monkeypatch):
    ut, job = ent
    scripts = []
    monkeypatch.setattr(ut, "_osascript_out", lambda script: scripts.append(script) or "Terminal")
    monkeypatch.setattr(ut, "_tty_of", lambda pid: {123: "/dev/ttys001", 456: "/dev/ttys009"}[pid])
    (job / "runners").write_text("456 earlier-birth\n123 current-birth\n")
    out = ut.press_enter(job)
    assert out["ok"] is True and out["tty"] == "/dev/ttys001", out
    assert len(scripts) == 1 and '"/dev/ttys001"' in scripts[0] and "ttys009" not in scripts[0]


def test_enter_refuses_before_the_command_runs(ent):
    ut, job = ent
    (job / "status.json").write_text(json.dumps({"status": "waiting_for_user"}))
    out = ut.press_enter(job)
    assert out["ok"] is False and "pressed" not in out


def test_enter_refuses_a_finished_job(ent):
    ut, job = ent
    (job / "status.json").write_text(json.dumps({"status": "ran", "exit_code": 0}))
    out = ut.press_enter(job)
    assert out["ok"] is False and "ended" in out["error"]


def test_enter_window_not_found_is_an_error_not_a_claim(ent, monkeypatch):
    ut, job = ent
    monkeypatch.setattr(ut, "_osascript_out", lambda script: "")
    out = ut.press_enter(job)
    assert out["ok"] is False and "pressed" not in out


def test_relayed_enter_only_for_the_origin_gateway(ent, monkeypatch):
    ut, job = ent
    calls = []
    monkeypatch.setattr(ut, "_osascript_out", lambda script: calls.append(script) or "Terminal")
    out = ut.start_relayed("", "", "", {"gateway": "origin-B", "job_id": job.name, "enter": True})
    assert out["ok"] is False and out["code"] == "job_not_found" and out["enter"] is True and calls == []
    out = ut.start_relayed("", "", "", {"gateway": "origin-A", "job_id": job.name, "enter": True})
    assert out["ok"] is True and out["enter"] is True and out["pressed"] == "Terminal" and len(calls) == 1


def test_enter_remote_through_an_old_mac_is_never_reported_pressed(ent, monkeypatch, tmp_path):
    ut, _ = ent
    monkeypatch.setattr(ut, "pick_courier", lambda base: ("macbook", ""))
    monkeypatch.setattr(ut, "_sender", lambda session: {})
    # an old courier treats it as a status poll; an old Mac module refuses the empty command
    for answer in ({"ok": True, "status": "running", "job_id": "canonical-job"},
                   {"ok": False, "error": "command is required"}):
        monkeypatch.setattr(ut, "relay_once", lambda *a, _r=answer, **kw: dict(_r))
        out = ut.enter_remote(tmp_path / "gw", "canonical-job")
        assert out["ok"] is False and "pressed" not in out and "update" in out["error"], out


def test_enter_remote_relays_the_macs_answer(ent, monkeypatch, tmp_path):
    ut, _ = ent
    sent = []
    monkeypatch.setattr(ut, "pick_courier", lambda base: ("macbook", ""))
    monkeypatch.setattr(ut, "_sender", lambda session: {})
    monkeypatch.setattr(ut, "relay_once", lambda base, dev, args, *a, **kw: sent.append(args) or
                        {"ok": True, "status": "running", "job_id": "canonical-job", "pressed": "iTerm", "enter": True})
    out = ut.enter_remote(tmp_path / "gw", "canonical-job")
    assert out["ok"] is True and out["pressed"] == "iTerm" and out["device"] == "macbook" and "enter" not in out
    assert sent[-1]["enter"] is True and sent[-1]["job_id"] == "canonical-job"


def test_handle_remote_routes_action_enter(ent, monkeypatch):
    ut, _ = ent
    seen = []
    monkeypatch.setattr(ut, "owner_refusal", lambda session: "")
    monkeypatch.setattr(ut, "enter_remote", lambda base, job_id, session=None: seen.append(job_id) or {"ok": True})
    assert json.loads(ut.handle_remote({"action": "enter", "job_id": "canonical-job"}))["ok"] is True
    assert seen == ["canonical-job"]

"""agent-7e5ac22ea5 (recurrence): the user said in chat that the Enter-gated step was done ("im signed in"), which
yields the waiting tool call. The yielded result carried only "Read it and answer; keep waiting": no output and no
action=enter hint, so the agent waited 420 s instead of pressing Return. The yielded result must show what the
window is waiting on and say to call action=enter when that line asks for Enter. No real GUI or courier."""
import importlib.util
import json
from pathlib import Path

import pytest

PROMPT = "1) Sign in to claude.ai as you@example.com, then press Enter here."


@pytest.fixture
def ut(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("enter_yield_subject", Path(__file__).parents[1] / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(mod, "_osascript_out", lambda script: pytest.fail("unexpected GUI call"))
    monkeypatch.setattr(mod, "_yield_requested", lambda: True)  # a chat message arrives while waiting
    return mod


def _local_job(ut, output):
    job = ut.jobs_dir() / "local-job"
    job.mkdir(parents=True)
    (job / "status.json").write_text(json.dumps({"status": "running"}))
    (job / "output.log").write_text(f"Opening browser...\r\n{output} ")
    return job


def test_local_yield_offers_enter_when_the_window_waits_for_it(ut):
    res = ut.wait_for(_local_job(ut, PROMPT), 5)
    assert res["yielded"] is True and res["status"] == "running"
    assert res["waiting_for_enter"] == PROMPT
    assert "action=enter" in res["note"] and "Read it and answer" in res["note"]


def test_local_yield_never_offers_enter_for_other_prompts(ut):
    res = ut.wait_for(_local_job(ut, "Password:"), 5)
    assert res["yielded"] is True and "waiting_for_enter" not in res
    assert "action=enter" not in res["note"]


@pytest.fixture
def remote(ut, monkeypatch):
    """A relayed wait: each status poll answers with what the Mac's window shows, until the user's chat message
    cuts a poll short (relay_once's yielded stub carries no output)."""
    monkeypatch.setattr(ut, "owner_refusal", lambda session: "")
    monkeypatch.setattr(ut, "pick_courier", lambda base: ("macbook", ""))
    monkeypatch.setattr(ut, "_sender", lambda session: {"session_id": "s1"})
    monkeypatch.setattr(ut, "_yield_requested", lambda: False)  # only relay_once sees the message here
    calls = []

    def install(*answers):
        queue = list(answers)

        def fake(base, device, args, sender, result_wait, yieldable=False, rid=None):
            calls.append({"args": dict(args), "yieldable": yieldable})
            assert yieldable, "a yield must not cost another courier round trip"
            return dict(queue.pop(0)) if queue else {"ok": True, "status": "pending", "yielded": True}
        monkeypatch.setattr(ut, "relay_once", fake)
        return calls
    return install


def test_relayed_yield_shows_the_window_and_offers_enter(ut, remote):
    calls = remote({"ok": True, "status": "running", "job_id": "mac-job", "output": f"Opening...\n{PROMPT}"})
    res = json.loads(ut.handle_remote({"job_id": "mac-job", "wait_s": 900}))
    assert len(calls) == 2  # one full poll, then the poll the user's message cut short
    assert res["yielded"] is True and res["status"] == "running" and res["device"] == "macbook"
    assert res["waiting_for_enter"] == PROMPT and PROMPT in res["output"]
    assert "action=enter" in res["note"] and "Read it and answer" in res["note"]


def test_relayed_yield_before_any_poll_answered_says_how_to_check(ut, remote):
    remote()  # the very first poll is cut short: nothing is known about the window yet
    res = json.loads(ut.handle_remote({"job_id": "mac-job", "wait_s": 900}))
    assert res["ok"] is True and res["yielded"] is True and "waiting_for_enter" not in res
    assert res["note"].startswith("The user sent a message") and "wait_s=1" in res["note"]


def test_relayed_yield_without_an_enter_prompt_does_not_offer_enter(ut, remote):
    remote({"ok": True, "status": "running", "job_id": "mac-job", "output": "Password:"})
    res = json.loads(ut.handle_remote({"job_id": "mac-job", "wait_s": 900}))
    assert res["yielded"] is True and res["output"] == "Password:" and "waiting_for_enter" not in res
    assert "press Return there" not in res["note"]

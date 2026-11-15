"""Focus safety and narrow API contracts, with no real process or GUI access."""
import importlib.util
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def focus(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("focus_contract_subject", Path(__file__).parents[1] / "__init__.py")
    ut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ut)
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ut, "_proc_start", lambda pid, **kw: "current-birth")
    monkeypatch.setattr(ut, "_tty_of", lambda pid: "/dev/ttys001")
    monkeypatch.setattr(ut, "_osascript_out", lambda script: pytest.fail("unexpected GUI call"))
    monkeypatch.setattr(ut, "relay_once", lambda *a, **kw: pytest.fail("unexpected courier publication"))
    monkeypatch.setattr(ut.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected subprocess"))
    job = ut.jobs_dir() / "canonical-job"
    job.mkdir(parents=True)
    (job / "status.json").write_text(json.dumps({"status": "running"}))
    (job / "runners").write_text("123 current-birth\n")
    (job / "meta.json").write_text(json.dumps({"origin": {"gateway": "origin-A"}}))
    return ut, job


def test_unknown_local_job_has_validation_diagnostic(focus):
    ut, _ = focus
    out = json.loads(ut.handle({"action": "focus", "job_id": "absent-job"}))
    assert out["ok"] is False
    assert "unknown job_id absent-job" in out["error"]
    assert "focus needs" in out["error"]


def test_reused_pid_cannot_focus_its_new_window(focus, monkeypatch):
    ut, job = focus
    (job / "runners").write_text("123 previous-birth\n")
    calls = []
    monkeypatch.setattr(ut, "_osascript_out", lambda script: calls.append(script) or "Terminal")
    out = ut.focus_window(job)
    assert out["ok"] is False
    assert calls == []
    assert "no live window" in out["error"]


def test_empty_tty_is_filtered_before_gui(focus, monkeypatch):
    ut, job = focus
    calls = []
    monkeypatch.setattr(ut, "_tty_of", lambda pid: "")
    monkeypatch.setattr(ut, "_osascript_out", lambda script: calls.append(script) or "")
    out = ut.focus_window(job)
    assert calls == []
    assert out["ok"] is False and "no live window" in out["error"]


def test_no_runner_preserves_specific_diagnostic(focus):
    ut, job = focus
    (job / "runners").unlink()
    out = ut.focus_window(job)
    assert out["ok"] is False
    assert out["error"] == "no live window runs this job (its runner is gone or never started)"


@pytest.mark.parametrize("error_type", ["OpenFailed", "OpenTimedOut"])
def test_osascript_failure_is_a_structured_focus_result(focus, monkeypatch, error_type):
    ut, job = focus
    def denied(script):
        raise getattr(ut, error_type)("fixture denied")
    monkeypatch.setattr(ut, "_osascript_out", denied)
    out = ut.relayed_focus(job.name, "origin-A")
    assert out["ok"] is False and out["focus"] is True
    assert out["error"] == "could not bring the window forward: fixture denied"


@pytest.mark.parametrize("raw,expected", [("ttys001\n", "/dev/ttys001"), ("/dev/ttys001\n", "/dev/ttys001"),
                                          ("?\n", ""), ("??\n", ""), ("-\n", ""), ("\n", "")])
def test_ps_tty_normalization_and_placeholders(tmp_path, monkeypatch, raw, expected):
    spec = importlib.util.spec_from_file_location("tty_contract_subject", Path(__file__).parents[1] / "__init__.py")
    ut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ut)
    calls = []
    def ps(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(stdout=raw)
    monkeypatch.setattr(ut.subprocess, "run", ps)
    assert ut._tty_of(123) == expected
    assert calls == [["/bin/ps", "-o", "tty=", "-p", "123"]]


def test_foreign_gateway_cannot_focus_existing_job(focus, monkeypatch):
    ut, job = focus
    calls = []
    monkeypatch.setattr(ut, "_osascript_out", lambda script: calls.append(script) or "Terminal")
    out = ut.relayed_focus(job.name, "origin-B")
    assert out["ok"] is False and out["code"] == "job_not_found" and out["focus"] is True
    assert calls == []
    # Positive control: it is a focusable job, not a failure from an unusable fixture.
    assert ut.relayed_focus(job.name, "origin-A")["ok"] is True
    assert len(calls) == 1


def test_missing_relayed_job_returns_not_found(focus):
    ut, _ = focus
    out = ut.relayed_focus("absent-job", "origin-A")
    assert out["ok"] is False and out["code"] == "job_not_found" and out["focus"] is True


def test_second_runner_tty_is_tried_if_first_has_no_window(focus, monkeypatch):
    ut, job = focus
    (job / "runners").write_text("123 current-birth\n124 current-birth\n")
    monkeypatch.setattr(ut, "_tty_of", lambda pid: "/dev/ttys001" if pid == 123 else "/dev/ttys002")
    calls = []
    def gui(script):
        calls.append(script)
        return "Terminal" if '"/dev/ttys002"' in script else ""
    monkeypatch.setattr(ut, "_osascript_out", gui)
    out = ut.focus_window(job)
    assert out["ok"] is True and out["tty"] == "/dev/ttys002"
    assert len(calls) == 2
    assert '"/dev/ttys001"' in calls[0] and '"/dev/ttys002"' in calls[1]


def test_courier_error_short_circuits_publication(focus, monkeypatch, tmp_path):
    ut, _ = focus
    calls = []
    monkeypatch.setattr(ut, "pick_courier", lambda base: ("", "multiple couriers; choose one"))
    monkeypatch.setattr(ut, "relay_once", lambda *args: calls.append(args) or {"focus": True, "ok": False})
    monkeypatch.setattr(ut, "_sender", lambda session: {})
    out = ut.focus_remote(tmp_path / "gateway" / "outbox", "canonical-job")
    assert calls == []
    assert out == {"ok": False, "job_id": "canonical-job",
                   "error": "cannot reach the owner's Mac to raise the window: multiple couriers; choose one"}


@pytest.mark.parametrize("keep_request_metadata", [False, True])
def test_ledger_alias_routes_with_or_without_retained_request_metadata(focus, monkeypatch, tmp_path, keep_request_metadata):
    """Missing request_id is repaired/legacy state, not a claim that current starts omit it."""
    ut, job = focus
    rid = "a" * 32
    origin = {"gateway": "origin-A"}
    if keep_request_metadata:
        origin["request_id"] = rid
    (job / "meta.json").write_text(json.dumps({"origin": origin}))
    base = tmp_path / "gateway" / "outbox"
    ut.ledger_put(base, {"request_id": rid, "job_id": job.name, "created": time.time(), "state": "started"})
    assert ut.ledger_lookup(base, rid)["job_id"] == job.name
    assert ut.find_relayed_by_request(rid, "origin-A") == (job if keep_request_metadata else None)
    monkeypatch.setattr(ut, "pick_courier", lambda base: ("fixture-mac", ""))
    monkeypatch.setattr(ut, "_sender", lambda session: {"gateway": "origin-A"})
    monkeypatch.setattr(ut, "_osascript_out", lambda script: "Terminal")
    def transport(base, device, args, sender, timeout):
        assert args["focus"] is True
        return ut.relayed_focus(args["job_id"], sender["gateway"])
    monkeypatch.setattr(ut, "relay_once", transport)
    out = ut.focus_remote(base, rid)
    assert out["ok"] is True and out["job_id"] == job.name and out["focused"] == "Terminal"
    assert out["device"] == "fixture-mac"
    assert "focus" not in out  # internal transport marker is not part of the public API

"""gateway-9aaf151a15 (came back again): a provider refusal's automatic retry is not a backend stop.

2026-11-21 05:37:58Z a turn of chat ...5a8ef0 ended `status=error failure_reason=rate_limit` (HTTP 429, quota); core's
refused-turn retry timer replayed it 45 s later as an auto-continue whose note says "the app or its backend process
stopped" (session_auto_continue._auto_continue_note). The backend (pid 2801475) stayed alive; the coach read the note
as a crash, found no stop cause (unknown) and refiled the old fingerprint although the earlier fix (restart-proof
launches) had nothing to do with it. Core's gui.log names the real event, so an unknown-cause crash note whose chat's
previous turn ended in an error is a retry, not a stop.
"""
import datetime as _dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_provider_retry_test", ROOT / "self_heal.py")
core = load("coach_core_provider_retry_test", ROOT / "core.py")
plugin = load("coach_plugin_provider_retry_test", ROOT / "__init__.py")

T0 = 1_800_000_000.0
SID = "20261101_012339_5a8ef0"
CRASH_NOTE = ("[System note: Your previous turn was interrupted mid-run — the app or its backend process stopped "
              "before the turn could finish. The interrupted request was:]\n\nfix the panel")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)


def _stamp(ts):
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S,000")


def _gui(home, *events):
    """events: (offset_s, 'accepted'|'finished', fields) in the real gui.log shapes."""
    (home / "logs").mkdir(parents=True, exist_ok=True)
    lines = []
    for off, kind, f in events:
        if kind == "accepted":
            lines.append(f"{_stamp(T0 + off)} INFO tui_gateway.server: tui prompt accepted: ui_session=f9d6f6ea "
                         f"session_key=202611...8ef0 agent_session_id={f.get('sid', SID)} kind={f['kind']} chars=924 images=0")
        else:
            lines.append(f"{_stamp(T0 + off)} INFO [{f.get('sid', SID)}] tui_gateway.server: tui turn finished: "
                         f"ui_session=f9d6f6ea session_key=202611...8ef0 agent_session_id={f.get('sid', SID)} "
                         f"status={f['status']} error_retained={f['status'] == 'error'} duration=24.6s"
                         + (f" failure_reason={f['reason']}" if f.get("reason") else ""))
    with open(home / "logs" / "gui.log", "a") as fh:
        fh.write("\n".join(lines) + "\n")


def _run(tmp_path, monkeypatch, setup):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(sh, "_pmset_log", lambda: [], raising=False)
    setup(home)
    now = [T0]
    store = core.Store(tmp_path / "coach", clock=lambda: now[0])

    def llm(system, prompt):
        return json.dumps({"p_defect": 0.0, "kind": "complaint", "component": "desktop", "symptom": "x"})
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    live = plugin._load("self_heal")
    monkeypatch.setattr(live, "_pmset_log", lambda: [], raising=False)
    hooks["pre_llm_call"](session_id=SID, user_message="fix the panel")
    now[0] = T0 + 1800
    hooks["pre_llm_call"](session_id=SID, user_message=CRASH_NOTE)
    log = tmp_path / "coach" / "log.jsonl"
    rows = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return [r for r in rows if r.get("event") == "selfheal_detect"], [r for r in rows if r.get("event") == "selfheal_skip"]


def test_retry_after_a_provider_refusal_is_not_a_backend_stop(tmp_path, on, monkeypatch):
    def setup(h):
        _gui(h, (1700, "accepted", {"kind": "auto_continue"}),
             (1724, "finished", {"status": "error", "reason": "rate_limit"}),
             (1769, "accepted", {"kind": "auto_continue"}))  # core's refused-turn retry, 45 s later
    detect, skip = _run(tmp_path, monkeypatch, setup)
    assert detect == []
    assert [s["why"] for s in skip] == ["provider_retry"] and "rate_limit" in skip[0]["evidence"]


def test_real_crash_after_an_earlier_errored_turn_still_files(tmp_path, on, monkeypatch):
    """A later turn ran to completion after the error: the cut turn is not the errored one, so no exemption."""
    def setup(h):
        _gui(h, (1000, "finished", {"status": "error", "reason": "rate_limit"}),
             (1100, "accepted", {"kind": "user"}), (1200, "finished", {"status": "complete"}),
             (1300, "accepted", {"kind": "user"}))
    detect, skip = _run(tmp_path, monkeypatch, setup)
    assert len(detect) == 1 and detect[0]["stop_cause"] == "unknown" and skip == []


def test_other_chats_errors_do_not_exempt(tmp_path, on, monkeypatch):
    def setup(h):
        _gui(h, (1724, "finished", {"status": "error", "reason": "rate_limit", "sid": "other_chat"}),
             (1769, "accepted", {"kind": "auto_continue", "sid": "other_chat"}))
    detect, _ = _run(tmp_path, monkeypatch, setup)
    assert len(detect) == 1


def test_a_known_stop_cause_wins_over_the_retry_evidence(tmp_path, on, monkeypatch):
    def setup(h):
        _gui(h, (1724, "finished", {"status": "error", "reason": "rate_limit"}),
             (1769, "accepted", {"kind": "auto_continue"}))
        (h / "logs").mkdir(exist_ok=True)
        with open(h / "logs" / "gateway-exit-diag.log", "a") as fh:
            fh.write(json.dumps({"ts": _dt.datetime.fromtimestamp(T0 + 1750, _dt.timezone.utc).isoformat(),
                                 "tag": "asyncio.run.SystemExit", "pid": 77, "code": 75}) + "\n")
    detect, skip = _run(tmp_path, monkeypatch, setup)
    assert len(detect) == 1 and detect[0]["stop_cause"] == "gateway shutdown" and skip == []


RETRY_NOTE = ("[System note: Your previous turn was interrupted mid-run — "
              "the provider refused the turn with a retryable error; the backend did not stop. "
              "Some of the work may already be complete; check the current state before redoing anything, then "
              "finish the task. The interrupted request was:]\n\nfix the panel")


@pytest.mark.parametrize("gap_s", [None, 45, 180, 7200])
def test_explicit_provider_retry_note_never_signals_a_crash(gap_s):
    assert sh.is_resume_note(RETRY_NOTE)
    assert sh.resume_signal(RETRY_NOTE, gap_s=gap_s) is None


def test_retry_mark_in_original_request_does_not_hide_real_crash():
    # As with pause marks, classification is limited to the machine-written header.
    text = CRASH_NOTE + "x" * 400 + "the provider refused the turn with a retryable error"
    assert sh.resume_signal(text)["symptom"] == sh.CRASH_SYMPTOM
    assert sh.resume_signal("User quoting: " + RETRY_NOTE) is None


def test_missing_or_unreadable_gui_log_fails_open(tmp_path, on, monkeypatch):
    detect, skip = _run(tmp_path, monkeypatch, lambda h: None)
    assert len(detect) == 1 and skip == []

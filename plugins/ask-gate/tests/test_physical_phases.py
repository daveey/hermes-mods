"""A hands-on step and a passive observation window are separate phases (self-heal kanban-0485576eb6).

A ring day-test was filed as ONE blocked human ask covering four gestures plus 24 hours of normal use, with a
resolved_check needing a recording >= 20 h after the start. David did the gestures and the card stayed blocked on
the clock. These tests drive the full pre_tool_call path with the judge returning ``ok`` (the structured judge result
path), so only the deterministic phase guard can hold the ask.
"""
import pytest
from conftest import llm_returning

DAY_TEST = {
    "action": ("Wear the ring for 24 hours of normal use and do the four gestures (tap, double tap, hold, swipe). "
               "Unblock = analyse the recording; archive = abandon the test."),
    "why_human": "Only David can wear the ring and perform the gestures",
    "scope": "DEVICE: test-ring\nEVIDENCE: ring-day-test-1",
    "evidence": "terminal inspected capture inventory",
    "interaction": "physical",
    "resolved_check": ("f=$(ls -t /captures/ring-*.json | head -1) && "
                       "test $(( $(date +%s) - $(stat -c %Y /captures/start) )) -ge 72000 && "
                       "grep -c gesture \"$f\" | grep -qx 4"),
}


def invoke(gate, request, tool="kanban_create", verdict="ok", **extra):
    gate.LOG.add("s1", "terminal", "capture inventory", "capture absent")
    gate.LLM = llm_returning([(verdict, "physical action")])
    return gate.pre_tool_call(tool_name=tool, session_id="s1", args={
        "initial_status": "blocked", "kind": "needs_input", "human_request": request, **extra})


def ask(**changes):
    request = dict(DAY_TEST)
    request.update(changes)
    return {k: v for k, v in request.items() if v is not None}


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_block"])
def test_mixed_gestures_and_observation_window_is_held(gate, tool):
    result = invoke(gate, ask(), tool, task_id="t_ring0001")
    assert result and result["action"] == "block"
    assert "physical_ask_phases" in result["message"]
    # The hint routes the passive wait to scheduling and never treats the step as success.
    assert "kanban_schedule" in result["message"]
    assert "not evidence" in result["message"]


@pytest.mark.parametrize("window", [
    "keep the watch on for a full day", "use the app normally over the next 48 hours",
    "wear it overnight", "at least 20 hours of wear", "a 24h soak"])
def test_window_phrasings_are_recognised(gate, window):
    result = invoke(gate, ask(action=f"Do the gestures, then {window}. Unblock = analyse; archive = drop.",
                              resolved_check="test -s /captures/gestures.json"))
    assert result and "physical_ask_phases" in result["message"]


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_block"])
def test_historical_abbreviated_window_with_script_probe(gate, tool):
    # Preserve producer syntax; scrub only identifiers, never paraphrase the failure.
    request = ask(
        action=("For the next 24 h, keep the device app closed and use your ring normally. "
                "Record at least one note while your phone is away or locked for over an hour. "
                "Also do one tap, one double tap, one tap-then-hold and one hold, each with no speech. "
                "Then reply with roughly how many notes you made and whether you saw any pairing prompt. "
                "Unblock = I compare app pulls against your notes; archive = skip the test "
                "(Phase A stays unproven)."),
        scope="24 h of normal ring use; read-only on the ring; device app stays installed.",
        resolved_check="~/.hermes/evidence/device-direct-sync/day_test_done.sh")
    result = invoke(gate, request, tool, task_id="t_ring0001")
    assert result and "physical_ask_phases" in result["message"]
    assert "kanban_schedule" in result["message"]
    assert "never complete" in result["message"]


@pytest.mark.parametrize("window", [
    "For the next 24 h, record a note", "for 24h, record a note",
    "normal ring use", "regular wrist device usage", "ordinary smart-watch wear",
])
def test_abbreviated_and_device_qualified_windows(gate, window):
    result = invoke(gate, ask(
        action=f"{window}. Unblock = analyse; archive = drop.",
        resolved_check="~/.hermes/evidence/device-direct-sync/day_test_done.sh"))
    assert result and "physical_ask_phases" in result["message"]


@pytest.mark.parametrize("action", [
    "Open normal settings on the phone once", "Unlock the phone for the next 24 help prompts",
    "Open the normal ring settings and use the pairing button once",
])
def test_device_words_do_not_bridge_unrelated_clauses(gate, action):
    assert invoke(gate, ask(action=f"{action}. Unblock = inspect; archive = drop.",
                            resolved_check="test -s /captures/open.json")) is None


def test_completed_step_plus_time_only_wait_is_not_a_human_ask(gate):
    result = invoke(gate, ask(
        scope=DAY_TEST["scope"] + "\nHUMAN_STEP: done (gestures recorded at 09:05)\nOBSERVATION: 24 h normal use",
        resolved_check="test -s /captures/gestures.json"), "kanban_block", task_id="t_ring0001")
    assert result and "physical_ask_phases" in result["message"]
    assert "kanban_schedule" in result["message"] and "no human step remains" in result["message"]


def test_elapsed_time_probe_cannot_gate_the_human_step(gate):
    # Phases are labelled, but the probe still waits on the clock: doing the step early could never clear it.
    result = invoke(gate, ask(scope=DAY_TEST["scope"] + "\nHUMAN_STEP: four ring gestures\n"
                                    "OBSERVATION: 24 h normal use, scheduled on t_ring0002"))
    assert result and "physical_ask_phases" in result["message"]
    assert "resolved_check" in result["message"]


def test_separated_outstanding_step_with_timer_remains_allowed(gate):
    # A genuine outstanding device act stays a human ask even when an observation window also runs.
    assert invoke(gate, ask(
        action="Unlock the test phone once so the capture can start. Unblock = start; archive = drop.",
        scope=DAY_TEST["scope"] + "\nHUMAN_STEP: unlock the test phone once\n"
                                  "OBSERVATION: 24 h normal use, scheduled on t_ring0002",
        resolved_check="test -s /captures/unlock.json")) is None


def test_new_build_open_once_without_window_remains_allowed(gate):
    assert invoke(gate, ask(action="Open the new build once on the test phone. Unblock = inspect; archive = drop.",
                            resolved_check="test -s /captures/open.json")) is None


@pytest.mark.parametrize("label", ["HUMAN_STEP: unknown", "HUMAN_STEP: maybe done?"])
def test_unknown_completion_is_not_inferred_done(gate, label):
    # Unknown completion is neither waived into a schedule nor treated as a finished test.
    assert invoke(gate, ask(scope=DAY_TEST["scope"] + f"\n{label}\nOBSERVATION: 24 h normal use, scheduled on "
                                                      "t_ring0002", resolved_check="test -s /captures/g.json")) is None


def test_user_report_alone_does_not_complete_or_waive_the_test(gate):
    # "David said he did it" retires the human step, not the observation acceptance: the hint keeps the test open.
    result = invoke(gate, ask(
        scope=DAY_TEST["scope"] + "\nHUMAN_STEP: completed per David's report\nOBSERVATION: 24 h normal use",
        resolved_check="test -s /captures/gestures.json"), "kanban_block", task_id="t_ring0001")
    assert result and "physical_ask_phases" in result["message"]
    assert "keep" in result["message"] and "acceptance" in result["message"]
    assert "never complete" in result["message"]


def test_non_physical_reply_about_a_soak_is_untouched(gate):
    # A decision about an observation window is not a physical ask; this guard does not apply.
    assert invoke(gate, ask(interaction="reply", action="Approve a 24h soak of the new build? Unblock = soak; "
                                                       "archive = skip.")) is None

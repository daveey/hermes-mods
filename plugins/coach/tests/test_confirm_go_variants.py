"""confirm rule, tile-shaped form (self-heal agent-f0722b8b79). David approved a repo transfer, then named the renamed
org; the agent filed a `session_outcome` decide tile "which name?" whose choices were "Go now: <a>" / "Go now: <b>" /
"Hold" and recommended <a> in its own reply. Nothing ran for 2 hours. CONFIRM_RE only matches question wording in the
reply, so the coach never judged it. Now the trigger is the tool call: a decide (or a reply's option list) with two
or more go choices puts `confirm` on the candidate list, and the judge and nudge say what a go-variant ask is.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_govar_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")
ar = _mod("agent_rules")

ASK = "we renamed metta-ai to softmax. what about example-org/example-repo?"
REPLY = ("Filed a tile for the target name. example-org/example-repo matches your phrasing, so that is the one I "
         "recommend; the other option is example-org/repo.")
DECIDE = {"kind": "decide", "items": [{"question": "Which name should the transferred repo get?", "choices": [
    {"label": "Go now: example-org/example-repo", "value": "a"},
    {"label": "Go now: example-org/repo", "value": "b"},
    {"label": "Hold", "value": "hold"}]}]}
ACCEPTED = json.dumps({"ok": True, "declared": True, "steps": ["us_1"]})
REFUSED = json.dumps({"ok": False, "error": "items[0] offers 2 go choices ...: the run is settled"})


def _log(args=DECIDE, result=ACCEPTED, tool="session_outcome"):
    coach = ar.AgentCoach()
    coach.observe("s", tool, args, result)
    return list(coach.sessions["s"]["turn_log"])


def test_the_incident_reply_alone_is_not_a_confirm_candidate():
    assert not rc.CONFIRM_RE.search(REPLY[-700:]) and not rc.recommend_stall(REPLY)
    assert "confirm" not in rc.prefilter(REPLY)


def test_a_go_variant_decide_shows_in_the_tool_log():
    log = _log()
    line = next(l for l in log if l.startswith("   <- decide go_variants=2"))
    assert "Go now: example-org/example-repo" in line and "Go now: example-org/repo" in line
    assert "refused" not in line


def test_a_refused_go_variant_decide_is_marked_refused():
    assert any(l.startswith("   <- decide go_variants=2") and "refused" in l for l in _log(result=REFUSED))


def test_a_go_variant_decide_via_tool_call_is_seen():
    log = _log(args={"calls": [{"name": "session_outcome", "arguments": DECIDE}]}, tool="tool_call")
    assert any(l.startswith("   <- decide go_variants=2") for l in log), log


def test_approve_hold_and_plain_choices_add_no_line():
    for labels in (["Approve: run it now", "Hold"], ["us-east-1", "eu-west-1"]):
        args = {"kind": "decide", "items": [{"question": "Run it?",
                                             "choices": [{"label": l, "value": l} for l in labels]}]}
        assert not any("go_variants" in l for l in _log(args=args)), labels


def test_the_tool_call_makes_confirm_a_candidate():
    assert "confirm" in rc.prefilter(REPLY, _log())
    assert "confirm" in rc.prefilter(REPLY, _log(result=REFUSED))  # a re-ask in prose after the refusal is judged
    assert rc.go_variant_ask(_log()).startswith("<- decide go_variants=2")


def test_a_reply_listing_two_go_options_is_a_candidate():
    reply = ("Which name should I use?\n\n- **Go now: example-org/example-repo**\n- **Go now: example-org/repo**\n"
             "- Hold")
    assert "confirm" in rc.prefilter(reply.replace("Which name should I use?", "Two names fit."))
    assert "confirm" not in rc.prefilter("Next steps:\n- Approve: run it now\n- Hold")


def test_go_variant_ask_is_judged_and_nudged_to_pick_the_default():
    calls = []

    def llm(system, prompt):
        calls.append((system, prompt))
        return json.dumps({"handoff": 0, "confirm": 0.9, "unverified": 0, "unwatched": 0, "report_only": 0,
                           "evidence": "tile asks which go option; reply recommends the first"})
    hit = rc.check(REPLY, ASK, _log(), llm, only=["confirm"],
                   screen=lambda cands: {"confirm": 0.0}, screen_below=0.2)
    assert len(calls) == 1 and "<- decide go_variants=2" in calls[0][1]
    assert hit["rule"] == "confirm" and hit["variant"] == "go_variants"
    assert "pick" in hit["nudge"] and "undo" in hit["nudge"]


def test_the_judge_is_told_what_a_go_variant_ask_is():
    sys_prompt = rc.judge_system()
    assert "go_variants" in sys_prompt and "why_not_default" in sys_prompt

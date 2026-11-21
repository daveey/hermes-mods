"""self-heal: a correction the coach asked for is not a fresh self-admission (agent-9c2de0075e).

The coach's metric_split rule fired on a final reply that gave scores as "bits per byte" with no split and asked: "If
the user saw it, correct it in your next message." The next turn (resumed in a new backend after a Hermes update)
corrected it, and the turn-end audit filed that correction as a new self-heal defect at p=0.96: the audit never saw
the coach's request. These tests pin:
  - correction-request hints (metric_split, card_claim) delivered into a turn are kept per session in the coach
    store, survive a backend restart and a resume note, and are replaced at the next real user turn;
  - the turn-end audit prompt shows them in a COACH CORRECTION REQUESTS section, and a turn with none gets the old
    prompt byte-for-byte;
  - a filed admission's saved audited turn carries them, so a re-judge under a later rule sees them too;
  - the section is part of AUDIT_RULE.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_self_heal_judged import events, plugin, sh  # noqa: E402
from test_self_heal_judged import store  # noqa: E402,F401  (fixture)

SID = "20261119_234712_deada5"
BARE = "AInstein's fixed 4-byte lookup scores 1.662 bits per byte, about the same as RL."
CORRECTION = ("I didn't check the split before. Correction to my last reply: the 1.662 baseline is validation bits "
              "per byte, per the chat's report.")
RESUME = ("[System note: Your previous turn was interrupted mid-run — it was paused at a step boundary for a Hermes "
          "update. The paused request was:]\n\nhow can we avoid this form of stall in the future")
NONE = json.dumps({"p_defect": 0.0, "kind": "none", "component": "agent", "symptom": ""})


class Judge:
    """Records every turn-end audit prompt; answers 'none' to every judge."""

    def __init__(self):
        self.audits = []

    def __call__(self, system, prompt):
        if "AGENT'S FINAL MESSAGE:" in prompt:
            self.audits.append(prompt)
        return NONE


def hooks(store, llm):
    _, h = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm, selfheal_confirm=None)
    return h


def turn(h, ask, reply, history=()):
    h["pre_llm_call"](session_id=SID, user_message=ask, platform="desktop", conversation_history=list(history))
    h["post_llm_call"](session_id=SID, assistant_response=reply, platform="desktop")


def test_requested_correction_reaches_the_audit_across_a_restart_and_a_resume(store):
    j = Judge()
    h = hooks(store, j)
    turn(h, "what did AInstein's controls show?", BARE)
    assert [r["rule"] for r in events(store, "agent_hint")] == ["metric_split"]
    # next user turn: the hint is delivered with its context; then the backend restarts mid-turn (Hermes update)
    h["pre_llm_call"](session_id=SID, user_message="how can we avoid this form of stall in the future",
                      platform="desktop", conversation_history=[{"role": "assistant", "content": BARE}])
    h2 = hooks(store, j)  # a new backend process: in-memory coach state is gone
    turn(h2, RESUME, CORRECTION, history=[{"role": "assistant", "content": BARE}])
    audit = j.audits[-1]
    assert "COACH CORRECTION REQUESTS DELIVERED TO THE AGENT THIS TURN" in audit
    assert '"1.662 bits per byte" with no split' in audit
    assert audit.index("COACH CORRECTION REQUESTS") < audit.index("AGENT'S FINAL MESSAGE:")
    # the next real user turn starts clean: an unrelated admission is judged without the old request
    turn(h2, "ok, and the card?", "I was wrong about the card: it is still open.",
         history=[{"role": "assistant", "content": CORRECTION}])
    assert "COACH CORRECTION REQUESTS" not in j.audits[-1]


def test_turn_without_correction_requests_keeps_the_old_prompt():
    old = (f"USER'S REQUEST:\n{sh._ask_text('ask') or '(unknown)'}\n\n"
           f"TOOL LOG THIS TURN:\n{sh._log_text(['-> terminal(ls)'], 30, 3500)}\n\n"
           f"AGENT'S FINAL MESSAGE:\nI was wrong.")
    assert sh.admission_prompt("I was wrong.", ["-> terminal(ls)"], "ask") == old
    assert sh.admission_prompt("I was wrong.", ["-> terminal(ls)"], "ask", coached=[]) == old
    assert sh.admission_prompt("I was wrong.", ["-> terminal(ls)"], "ask", coached=["  "]) == old


def test_filed_admission_saves_the_requests_and_a_rejudge_uses_them(monkeypatch):
    hint = 'Your last reply reports "1.662 bits per byte" with no split.'
    hit_json = json.dumps({"p_defect": 0.97, "kind": "admission", "component": "agent", "symptom": "bare metric"})
    hit = sh.detect_admission(CORRECTION, lambda s, p: hit_json, coached=[hint])
    assert hit and hit["audit_input"]["coached"] == [hint]
    seen = []
    monkeypatch.setattr(sh, "aux_judge", lambda s, p: seen.append(p) or NONE)
    monkeypatch.setattr(sh, "confirm_judge", lambda: None)
    sh._rejudge_admission(hit["audit_input"])
    assert hint in seen[-1] and "COACH CORRECTION REQUESTS" in seen[-1]


def test_coached_section_is_part_of_the_audit_rule():
    saved = sh.COACHED_SECTION
    try:
        sh.COACHED_SECTION = saved + "x"
        assert sh._audit_rule() != sh.AUDIT_RULE
    finally:
        sh.COACHED_SECTION = saved
    assert sh._audit_rule() == sh.AUDIT_RULE

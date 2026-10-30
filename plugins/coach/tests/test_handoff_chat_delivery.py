"""Asking the user to type a message into a Hermes chat is a handoff (self-heal agent-c25865e208).

A worker refused a cross_session send can deliver the text with a `FINAL SEND for @session:<ref>:` line in
kanban_complete; any other session sends it with cross_session. The judge prompt must say so, or "workers are refused
sending into Desktop chats" reads as a valid reason to hand the step to David.
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("coach_reply_check_cd", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)
FIXTURES = json.loads((HERE / "tests" / "fixtures" / "handoff_chat_delivery.json").read_text())


@pytest.mark.parametrize("fx", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_real_chat_delivery_handoffs_reach_the_judge(fx):
    assert "handoff" in rc.prefilter(fx["reply"])


def test_judge_prompt_says_chat_delivery_is_agent_doable():
    handoff_rule = rc.JUDGE_SYS[rc.JUDGE_SYS.index("- handoff:"):rc.JUDGE_SYS.index("- confirm:")]
    assert "FINAL SEND" in handoff_rule
    assert "cross_session" in handoff_rule
    assert "refused" in handoff_rule  # "workers are refused" is named as not a reason


@pytest.mark.parametrize("fx", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_chat_delivery_handoff_is_nudged_when_the_judge_scores_it(fx):
    seen = []

    def llm(system, user):
        seen.append(system)
        return json.dumps({"handoff": 0.93, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0,
                           "evidence": "asks the user to send a line into the canary repair chat"})
    hit = rc.check(fx["reply"], "work kanban task t_82f80db4", [], llm, only=["handoff"])
    assert hit and hit["rule"] == "handoff"
    assert "FINAL SEND" in seen[0]

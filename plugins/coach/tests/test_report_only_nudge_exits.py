"""The report_only nudge names every exit the report_only judges accept (self-heal agent-a679e846ca).

A chat close-out said an archived incident left Gmail delivery and Astra attribution unproven. That was true, and the
user had already withdrawn that proof (his "Close scoped" pick, recorded in a hub comment). The judges clear such an
item only when the reply names the user's decision AND a tool log shows where it is recorded (a card read or comment
carrying it). The nudge offered only fix / kanban_create / propose, so the agent rewrote the text ("per your
instruction") without reading the card, and the re-check failed it again. Two gaps:

1. the nudge never named the "recorded user decision" exit the rubric accepts;
2. a successful kanban_show logged only `<- OK task=... status=... last=commented`, never the comment text, so even
   a revision that read the card could not show the judge the decision.
"""
import importlib.util
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_roexits_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

CARD = "t_7e2ee45c00000000"
NOW = time.time()
DECISION = ("David picked Close scoped on t_01a1c74bb2549614: Gmail delivery and Astra attribution proof is withdrawn; "
            "archive the incident without it.")


# ---- 1. the nudge's exits match the rubric's -------------------------------------------------------------------
def test_rubric_still_accepts_a_recorded_user_decision():
    # The exit the nudge must name exists in both judges; if a rubric drops it, the nudge sentence goes too.
    for text in (rc.REPORT_ONLY_RULE, rc.CONFIRM_SYS):
        assert "defer, rescope or skip" in text, text[:200]
        assert "recorded" in text


def _variants():
    return {
        "default": rc._nudge_text("report_only", "Gmail delivery not proven"),
        "kanban_down": rc._nudge_text("report_only", "Gmail delivery not proven", kanban_down=True),
        "kanban_platform": rc._nudge_text("report_only", "Gmail delivery not proven", platform="kanban"),
    }


def test_every_accepted_exit_is_named_in_the_nudge():
    for name, text in _variants().items():
        low = text.lower()
        assert "fix" in low, name                                    # fix it
        assert "kanban_create" in text or "owner card" in low, name  # start it (or name the owner)
        assert "propose" in low, name                                # the user's call: propose the exact fix
        # the recorded user decision: read where it is recorded this turn and cite it
        assert "already chose to drop or rescope" in low, (name, text)
        assert "card id and date" in low, (name, text)
        assert "per your instruction" in low, (name, text)          # a bare claim does not count


def test_decision_exit_names_a_read_that_works_in_each_variant():
    v = _variants()
    assert "kanban_show" in v["default"]
    # this session's Kanban tools can't read the board: point at reads that still work
    assert "cross_task" in v["kanban_down"] and "hermes kanban show" in v["kanban_down"]


# ---- 2. a card read carries the decision comment into the judges' tool log ------------------------------------
def _show_with_comments():
    return {"task": {"id": CARD, "title": "BB model-balancing incident", "status": "archived"},
            "latest_summary": None, "parents": [], "children": [],
            "comments": [
                {"id": 188700, "author": "default", "body": "watchdog: still waiting on Gmail proof",
                 "created_at": int(NOW - 3 * 86400)},
                {"id": 188716, "author": "hub:gw:macbook", "body": DECISION, "created_at": int(NOW - 18 * 3600)},
            ],
            "events": [{"kind": "commented", "payload": None, "created_at": int(NOW - 18 * 3600), "run_id": None}],
            "runs": []}


def _turn():
    coach = rules.AgentCoach(None)
    sid = "s1"
    coach.observe(sid, "kanban_show", {"task_id": CARD}, json.dumps(_show_with_comments()))
    return coach, sid


def test_kanban_show_logs_the_newest_comment_text():
    coach, sid = _turn()
    rich = coach.turn_rich(sid)
    lines = [l for l in rich if "<- comments" in l]
    assert len(lines) == 1, rich
    line = lines[0]
    assert CARD in line and "Close scoped" in line and "hub:gw:macbook" in line, line
    assert "2026" in line or "T" in line  # dated
    assert len(line) <= rules.COMMENT_LINE_MAX, len(line)


def test_card_read_without_comments_adds_no_comment_line():
    coach = rules.AgentCoach(None)
    show = _show_with_comments()
    show["comments"] = []
    coach.observe("s2", "kanban_show", {"task_id": CARD}, json.dumps(show))
    assert not [l for l in coach.turn_rich("s2") if "<- comments" in l]


def test_decision_comment_reaches_the_outcome_confirm_prompt():
    """nudge_outcome with a stub screen judge: the revision read the card, and the decision comment reaches the
    strong second opinion's prompt (the judge that failed the revision twice)."""
    coach, sid = _turn()
    ask = "[kanban notice] t_7e2ee45c archived. Close out this chat."
    reply = ("The BB model-balancing incident is archived. Gmail delivery and Astra attribution were not verified: "
             f"you withdrew that proof with Close scoped on t_01a1c74bb2549614, recorded on {CARD} (Nov 11).")
    seen = {}

    def screen(system, prompt):
        return json.dumps({"report_only": 0.9, "evidence": "Gmail delivery not proven"})

    def confirm():
        seen["prompt"] = rc.confirm_prompt(ask, coach.turn_rich(sid), reply, board=[])
        return (0.1, "user decision recorded in the comment read this turn")

    _, log = coach.turn_context(sid)
    res = rc.nudge_outcome("report_only", reply, ask, log, screen, thresholds={"report_only": 0.5,
                                                                               "report_only_screen": 0.85},
                           confirm=confirm, kanban_down=False, wake=lambda: [], human_asks=lambda: [])
    assert "prompt" in seen, res
    assert "Close scoped" in seen["prompt"] and "<- comments" in seen["prompt"], seen["prompt"][-1500:]
    assert res["resolved"] is True, res

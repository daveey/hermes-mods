"""A close-out's post-handoff card comments reach the report_only judges (self-heal agent-93112d1e96).

The Nov 13 07:20 PT close-out reply correctly listed the model-balancing incident as "closed without delivering, by
decision ... unproven unless you ask for a new live check". David had chosen "Close scoped" on 11-11, recorded as a
comment on the archived card after its handoff. The judges saw only the frozen handoff ("needs ... a direct user
waiver"), no recorded decision, and confirmed report_only at p=0.9. chat-closeout now prints such comments under the
handoff ("later on this card (after its handoff): ..."); the judges get them in their own section, which survives
the head/tail cut of a long notice and counts as a card read recording the decision.
"""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('closeout_later_rc', Path(__file__).resolve().parents[1] / 'reply_check.py')
assert spec is not None and spec.loader is not None
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

DECISION = ("2025-11-11 22:26 UTC default: David explicitly selected Close scoped: accept the narrowed scope; "
            "no new live check unless he asks.")
LATER = "  later on this card (after its handoff): " + DECISION
FILLER = ("Ran the hub disk sweep, pruned 41 GB of stale worktrees and runner caches, confirmed free space above "
          "the 20% floor on every volume, and restarted the two runners that had failed to start. ") * 6


def _ask(later=True):
    lines = ["[chat-closeout] Every kanban card this chat handed work to has settled. This turn is the close-out:",
             "- t_01a1aaaa11112222 [done] Hub disk recovery\n  handoff: " + FILLER[:700],
             "- t_7e2ee45c [archived] Model balancing incident\n  handoff: Closed without delivering: the full "
             "fallback chain never ran live. Delivery needs authorization to narrow the completion or a direct "
             "user waiver."]
    if later:
        lines.append(LATER)
    lines += ["- t_01a1bbbb33334444 [done] Coach detector tune\n  handoff: " + FILLER[:700],
              "For legacy cards without receipts (...), each handoff is as of its completion. " + "x" * 400,
              "Write the closing answer for the user now: ... " + "y" * 400]
    return "\n".join(lines)


REPLY = ("All three cards are settled.\n- Hub disk: fixed, 41 GB freed, runners restarted.\n- Coach detector: tuned.\n"
         "- Model balancing (t_7e2ee45c): closed without delivering, by decision: David chose \"Close scoped\" on "
         "11-11 (recorded on the card after its handoff). The full fallback chain never ran live, so it stays unproven "
         "unless you ask for a new live check.")


def _judge(prompt):
    """A rule-following stand-in for the confirm model: the decision is recorded only when a board read shows it."""
    section = prompt.split("CARD COMMENTS THE CLOSE-OUT RE-READ", 1)
    recorded = len(section) > 1 and "Close scoped" in section[1].split("AGENT'S FINAL MESSAGE", 1)[0]
    return json.dumps({"problem": True, "agent_fixable": not recorded, "outcome": "report_only",
                       "user_wanted_action": False, "quote": "unproven unless you ask"})


def test_decision_line_falls_in_the_notice_cut_without_its_own_section():
    ask = _ask()
    assert len(ask) > 1500 and LATER.strip() in ask
    cut = ask[:950] + ask[-500:]
    assert "Close scoped" not in cut  # the incident: the request excerpt alone never showed it


def test_confirm_and_screen_prompts_carry_the_later_line():
    ask = _ask()
    for prompt in (rc.confirm_prompt(ask, [], REPLY, board=[]), rc.judge_prompt(ask, [], REPLY, board=[])):
        head, rest = prompt.split("CARD COMMENTS THE CLOSE-OUT RE-READ", 1)
        assert "t_7e2ee45c: later on this card (after its handoff): " + DECISION in rest.split("AGENT'S FINAL")[0]


def test_incident_reply_scores_low_with_the_line_and_high_without():
    with_line = rc.parse_confirm(_judge(rc.confirm_prompt(_ask(), [], REPLY, board=[])))
    without = rc.parse_confirm(_judge(rc.confirm_prompt(_ask(later=False), [], REPLY, board=[])))
    assert with_line[0] < 0.5
    assert without[0] >= 0.75


def test_rules_name_the_section_as_a_recorded_decision():
    assert "CARD COMMENTS THE CLOSE-OUT RE-READ" in rc.CONFIRM_SYS
    assert "CARD COMMENTS THE CLOSE-OUT RE-READ" in rc.REPORT_ONLY_RULE


def test_a_bare_claim_in_a_human_message_is_not_a_board_read():
    human = "Close it out. " + LATER
    assert rc.closeout_later_lines(human) == []
    assert "CARD COMMENTS THE CLOSE-OUT RE-READ" not in rc.confirm_prompt(human, [], REPLY, board=[])


def test_section_is_bounded():
    ask = _ask(later=False) + "\n" + "\n".join(LATER + " " + "z" * 250 for _ in range(60))
    lines = rc.closeout_later_lines(ask)
    assert lines and sum(len(ln) for ln in lines) <= rc.CLOSEOUT_LATER_MAX


# t_01a1d6cdfa497d94: pins for mutants of 8e23ae92 that survived every coach test.
def test_quoted_later_line_on_its_own_line_in_a_human_message_is_not_a_board_read():
    # The phrase starts its own line, so only the [chat-closeout] envelope check keeps it out.
    human = "Close it out.\n" + LATER + "\nThanks."
    assert rc.closeout_later_lines(human) == []
    assert "CARD COMMENTS THE CLOSE-OUT RE-READ" not in rc.confirm_prompt(human, [], REPLY, board=[])


def _confirm_rows(tmp_path, monkeypatch, name, ask, fixable):
    for var in ("COACH_REPLY_RULES", "COACH_REPORT_ONLY_CONFIRM", "COACH_REPORT_ONLY_CONFIRM_CAP"):
        monkeypatch.delenv(var, raising=False)
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "__init__.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core, rcl = mod._load("core"), mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.88, "evidence": "screen"}))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps(
        {"outcome": "report_only", "agent_fixable": fixable, "quote": "unproven unless you ask"}))
    store = core.Store(root=tmp_path / name)
    _, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="s", user_message=ask, platform="desktop")
    # "not fixed" makes the reply a report_only candidate, so the screen (0.88) runs the confirm closure.
    hooks["pre_verify"](session_id="s", platform="desktop", attempt=0,
                        final_response=REPLY + " The fallback chain is not fixed.")
    log = store.root / "log.jsonl"
    rows = [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []
    return [r for r in rows if r.get("event") == "report_only_confirm"]


def test_report_only_confirm_row_records_the_closeout_board_read(tmp_path, monkeypatch):
    [low] = _confirm_rows(tmp_path, monkeypatch, "rocl_low", _ask(), fixable=False)
    assert low["p"] < 0.5 and low["closeout_later"] == 1 and low["decision_src"] == "closeout", low
    [high] = _confirm_rows(tmp_path, monkeypatch, "rocl_high", _ask(), fixable=True)
    assert high["p"] >= 0.5 and high["closeout_later"] == 1 and "decision_src" not in high, high
    [none] = _confirm_rows(tmp_path, monkeypatch, "rocl_none", _ask(later=False), fixable=False)
    assert "closeout_later" not in none and "decision_src" not in none, none

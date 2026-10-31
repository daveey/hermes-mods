"""report_only scope limits past the first 2000 characters (self-heal agent-668442d68f).

A Kanban worker's request opens with ~2 KB of card header, so a "Diagnosis only." at the end of the body fell past the
2000-character cut in turn_start; report_only nudged a diagnosis-only worker into changing cards. These tests drive the
real path: pre_llm_call (turn_start) -> pre_verify (turn_context -> report_only_guard / judge prompt).
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_scope_long_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")

FILLER = ("1. Read the parent card in full: body, comments and history, its park/wake log, and every child or "
          "dependency card in its graph.\n")


def kanban_prompt(body_tail: str, *, blockers: str = "", recent: str = "") -> str:
    """A Kanban worker request shaped like core's build_worker_context: header, ## Body, then board sections."""
    body = ("Context: card t_parent has parked 3 times on the same premise and is woken with nothing new.\n\n"
            "Goal: find out what is actually blocking the graph from completing.\n\nApproach:\n" + FILLER * 14
            + "\nAcceptance criteria: post a comment with a table of every open node and a one-paragraph conclusion.\n\n"
            + body_tail)
    return ("# Kanban task t_0123456789abcdef: Diagnose why the upkeep graph makes no progress between wakes\n\n"
            "Assignee: default\nStatus:   running\nWorkspace: scratch @ /tmp/ws/t_0123456789abcdef\n\n"
            f"## Body\n{body}\n\n"
            "## Open shared blockers on this board\nExternal walls other cards already hit.\n"
            f"- `some-wall` (0 card(s) parked): {blockers or 'CI quota refused on the runner pool.'}\n\n"
            "## Recent work by @default\n"
            f"- t_aaaa — Fix the export job (2026-10-30 19:07, 4m ago): {recent or 'merged and verified live.'}\n\n\n"
            "work kanban task t_0123456789abcdef")


LIMITED = kanban_prompt("Do not change any cards in this task. Diagnosis only.")
REPLY = ("Root cause: the shepherd's graph is waiting on a child routed to a profile that cannot run it. "
         "Unblock: reassign t_child to default. I have not changed anything; the routing is not fixed yet.")


def _verdict(p):
    return {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": p, "evidence": "screen"}


def _hooks(tmp_path, monkeypatch, name, verdict_p=0.96):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location(name, HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    prompts = []

    def judge(system, prompt):
        prompts.append(prompt)
        return json.dumps(_verdict(verdict_p))
    monkeypatch.setattr(rcl, "aux_judge", judge)
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: (prompts.append(p), json.dumps(
        {"outcome": "report_only", "agent_fixable": True, "quote": "stops at the diagnosis"}))[1])
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    return store, hooks, prompts


def _log(store):
    return [json.loads(line) for line in (store.root / "log.jsonl").read_text().splitlines()]


def test_fixture_puts_the_limit_past_2000_chars():
    assert len(LIMITED) > 2100 and LIMITED.index("Diagnosis only") > 2000
    assert not rc.scope_limited(LIMITED[:2000])  # the old cut could not see it


def test_long_kanban_request_is_scope_limited_through_the_real_hooks(tmp_path, monkeypatch):
    store, hooks, prompts = _hooks(tmp_path, monkeypatch, "coach_init_scope_long1")
    assert "report_only" in rc.prefilter(REPLY)
    hooks["pre_llm_call"](session_id="kw", user_message=LIMITED)
    hooks["post_tool_call"](session_id="kw", tool_name="kanban_show", args={"task_id": "t_parent"}, result="{}")
    assert hooks["pre_verify"](session_id="kw", attempt=0, final_response=REPLY) is None
    logged = _log(store)
    assert not any(r["event"] in ("reply_nudge", "report_only_confirm") for r in logged)
    checked = [r for r in logged if r["event"] == "reply_checked" and r["session_id"] == "kw"]
    assert checked and checked[-1].get("guard") == "scope_limited"
    assert checked[-1].get("ask_len") == len(LIMITED)  # detection: the check saw the whole request


def test_judge_and_confirm_prompts_carry_the_late_limit():
    assert "Diagnosis only" in rc.judge_prompt(LIMITED, [], REPLY)
    assert "Diagnosis only" in rc.confirm_prompt(LIMITED, [], REPLY)
    short = "why is the export job red?"
    assert rc.judge_prompt(short, [], REPLY).count(short) == 1  # short asks are not duplicated


def test_do_not_change_any_noun_is_a_scope_limit():
    for ask in ("Do not change any cards in this task.", "don't touch any files", "Don't modify any config.",
                "do not change any settings here"):
        assert rc.scope_limited(ask), ask


def test_long_request_without_a_limit_is_not_scope_limited(tmp_path, monkeypatch):
    plain = kanban_prompt("Fix the routing and re-dispatch the stuck children.",
                          blockers="read-only replica; diagnosis only for now, do not change anything",
                          recent="Analysis only: no edits were needed.")
    assert len(plain) > 2100
    assert not rc.scope_limited(plain)  # phrases in board sections are not the user's limit
    assert not rc.scope_limited("Please fix the export job. " * 120)
    store, hooks, _ = _hooks(tmp_path, monkeypatch, "coach_init_scope_long2")
    hooks["pre_llm_call"](session_id="kp", user_message=plain)
    out = hooks["pre_verify"](session_id="kp", attempt=0, final_response=REPLY)
    assert out and out["action"] == "continue"
    nudge = next(r for r in _log(store) if r["event"] == "reply_nudge")
    assert nudge["rule"] == "report_only" and nudge["ask_len"] == len(plain)


def test_human_text_limit_past_2000_chars_counts():
    ask = "Here is the log I pasted:\n" + "line of log output\n" * 120 + "\nJust tell me why it fails, don't fix it."
    assert len(ask) > 2100
    coach = _mod("agent_rules").AgentCoach(None)
    coach.turn_start("h1", ask)
    seen, _ = coach.turn_context("h1")
    assert rc.report_only_guard(seen, REPLY) == "scope_limited"


def test_standing_instructions_section_counts_as_the_users_text():
    ask = LIMITED.replace("Do not change any cards in this task. Diagnosis only.", "Look into it.").replace(
        "## Open shared blockers", "## Standing instructions (from the user; in force for this card)\n"
        "standing: analysis only, don't change anything\n\n## Open shared blockers")
    assert rc.scope_limited(ask)


def skill_block(name: str, body: str, supporting: bool = True) -> str:
    """A preloaded-skill block shaped like core's _build_skill_message."""
    out = (f'[IMPORTANT: The user launched this CLI session with the "{name}" skill preloaded. Treat its instructions '
           f"as active guidance for the duration of this session unless the user overrides them.]\n\n---\nname: {name}\n"
           f"---\n\n{body}\n\n[Skill directory: /skills/{name}]\nResolve any relative paths in this skill against that "
           "directory.")
    if supporting:
        out += (f"\n\n[This skill has supporting files (paths relative to the skill directory above):]\n- references/a.md"
                f'\n\nLoad any of these with skill_view(name="{name}", file_path="<path>"), or run scripts directly.')
    return out


def test_preloaded_skill_text_is_not_the_request(tmp_path, monkeypatch):
    skill_prose = ("Spend one quick read-only probe pass first. **Plan first** on design-heavy work. Analysis only: "
                   "never applies here.\n") * 400  # ~50 KB, like a Kanban worker with three skills preloaded
    rules = _mod("agent_rules")
    plain = kanban_prompt("Fix the routing and re-dispatch the stuck children.")
    msg = skill_block("five-whys", skill_prose) + "\n\n" + skill_block("plain", "Short.", supporting=False) + \
        "\n\n" + LIMITED
    assert rules.request_text(msg) == LIMITED
    assert rules.request_text(skill_block("x", skill_prose) + "\n\n" + plain) == plain
    assert rules.request_text(LIMITED) == LIMITED and rules.request_text("hi") == "hi"
    store, hooks, _ = _hooks(tmp_path, monkeypatch, "coach_init_scope_long3")
    hooks["pre_llm_call"](session_id="ks", user_message=msg)  # the card's limit lies ~50 KB in, past ASK_CAP raw
    assert hooks["pre_verify"](session_id="ks", attempt=0, final_response=REPLY) is None
    hooks["pre_llm_call"](session_id="kn", user_message=skill_block("x", skill_prose) + "\n\n" + plain)
    out = hooks["pre_verify"](session_id="kn", attempt=0, final_response=REPLY)
    assert out and out["action"] == "continue"  # skill prose is not the user's limit


def test_adjectival_x_only_in_a_long_request_is_not_a_limit():
    for ask in ("Read-only checks before any request for user action. Fix the router.",
                "Then run `active_pr_reason` read-only on the replica and fix the guard.",
                "The controller created the report-only chain; land the fix.",
                "Spend one quick read-only probe pass, then fix it."):
        assert not rc.scope_limited(ask), ask
    for ask in ("Diagnosis only.", "Read-only: why is the disk full?", "This is an analysis-only task.",
                "investigation only, please", "Research-only for now"):
        assert rc.scope_limited(ask), ask

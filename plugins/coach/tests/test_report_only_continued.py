"""report_only vs kanban_continue (self-heal agent-2d5f5e554b).

A Kanban worker out of runtime pushed a WIP commit and checkpointed its own card with kanban_continue ("a fresh run
will resume this card now ... do not call more tools"). Its closing reply listed what the next run will do. The
screen judge scored it report_only 0.88 ("claims a fresh run picks up work without shown scheduling"), the strong
second opinion was capped, and the screen nudged alone. The nudge pushed the worker into more tool calls and a
rewrite with a wrong fact. kanban_continue is the protocol's sanctioned hand-off of unfinished work to the next run:
a successful one this turn is a started fix, not a report.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_cont_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")

# The worker's ask and closing reply, shape kept, project details replaced (public repo).
ASK = ("work kanban task t_0000000000000001\n\nLane L8 of the build: port the filter, write the endpoints module, "
       "regenerate the manifest, write the gate files and open the PR into the integration branch.")
DRAFT = (
    "L8 isn't finished; I checkpointed the card and a fresh run picks it up now. The kernel code is pushed as WIP "
    "commit 680fcf201 on `lane-L8`. Tests (d)/(e) are unconfirmed: the full test file timed out. The docs, the "
    "manifest regeneration, the gate files and the PR are still to do.\n\n"
    "| Item | State |\n|---|---|\n| `kfilter.py` | Done. Tests (a)-(c) pass. |\n"
    "| `endpoints.py` | Written. Tests (d)/(e) unconfirmed: the full test file timed out at 300 s. |\n"
    "| docs, gate JSONs, PR | Not started. |\n\n"
    "**Next run:**\n- Fix `make_repo.sh` and add slow test (h).\n- Regenerate the manifest.\n- Run every test file.\n"
    "- Open the PR and request review.\n\nThe heavy variants will go to review untested.")
# Tool-log lines exactly as agent_rules.observe writes them (140-char arg cut; `<- ERROR` after a failed call).
CONT_DEFERRED = '-> tool_call({"calls": [{"name": "kanban_continue", "arguments": {"checkpoint": "## L8 checkpoint (run 1, ~52 min used)\\n\\n**Worktree**: `$HERMES_KANBA)'
CONT_DIRECT = '-> kanban_continue({"checkpoint": "## L8 checkpoint (run 1)\\n\\nWIP commit 680fcf201 pushed"})'
BEFORE = ["-> terminal(cd fr && git push origin lane-L8)", "-> tool_describe(kanban_continue)"]
OK_LOG = BEFORE + [CONT_DEFERRED]
ERR_LOG = BEFORE + [CONT_DIRECT, '   <- ERROR {"ok": false, "error": "task t_0000000000000001 is not running"}']


def _judge(p):
    calls = []

    def llm(system, prompt):
        calls.append((system, prompt))
        return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": p,
                           "evidence": "Claims a fresh run picks up work without shown scheduling"})
    return llm, calls


def _check(log, p=0.95, confirm=lambda: None, **kw):
    llm, calls = _judge(p)
    hit = rc.check(DRAFT, ASK, log, llm, threshold=0.7, thresholds=rc.settings()["thresholds"],
                   confirm=confirm, confirm_floor=rc.CONFIRM_FLOOR, **kw)
    return hit, calls


def test_reply_after_a_successful_kanban_continue_is_not_report_only():
    # the replayed turn: a high screen score (and a confirm that would agree) must not nudge
    for log in (OK_LOG, BEFORE + [CONT_DIRECT]):
        hit, _ = _check(log, p=0.95, confirm=lambda: (0.9, "next run does it"))
        assert hit is None, log
    assert rc.continued_run(OK_LOG) and rc.continued_run(BEFORE + [CONT_DIRECT])
    assert rc.report_only_guard(ASK, DRAFT, OK_LOG) == "continued_run"


def test_same_reply_without_kanban_continue_still_nudges():
    hit, _ = _check(BEFORE, p=0.95)
    assert hit and hit["rule"] == "report_only"
    assert not rc.continued_run(BEFORE)
    assert rc.report_only_guard(ASK, DRAFT, BEFORE) is None
    # a log line that only mentions the tool (describe, grep) is not a continue
    assert not rc.continued_run(["-> tool_describe(kanban_continue)", "-> terminal(grep -n kanban_continue x.py)"])


def test_kanban_continue_that_errored_still_nudges():
    assert not rc.continued_run(ERR_LOG)
    hit, _ = _check(ERR_LOG, p=0.95)
    assert hit and hit["rule"] == "report_only"


def test_kanban_continue_is_a_wake_source():
    assert "kanban_continue" in rc.WAKE_TOOLS
    assert "tool:kanban_continue" in rc.wake_sources("", OK_LOG)


def test_judge_and_confirm_prompts_know_kanban_continue():
    assert "kanban_continue" in rc.JUDGE_SYS and "kanban_continue" in rc.CONFIRM_SYS


def test_capped_confirm_does_not_let_the_screen_nudge_alone_when_told_not_to():
    # Kanban worker turns: a due-but-capped second opinion skips the nudge instead of trusting the screen.
    audit = []
    hit, _ = _check(BEFORE, p=0.97, confirm=lambda: None, capped_nudge=False, audit=audit.append)
    assert hit is None
    assert audit and audit[0]["source"] == "screen_capped_skip" and audit[0]["passing"] is False
    # the confirm, when it runs, still decides
    hit, _ = _check(BEFORE, p=0.97, confirm=lambda: (0.9, "stops at the diagnosis"), capped_nudge=False)
    assert hit and hit["rule"] == "report_only"
    # default (interactive chats) keeps the stricter capped bar
    hit, _ = _check(BEFORE, p=0.97, confirm=lambda: None)
    assert hit and hit["verdict"]["report_only_src"] == "screen_fallback"


def test_confirm_daily_cap_raised():
    assert rc.CONFIRM_DAILY_CAP >= 200


def _hooks(tmp_path, monkeypatch, verdict_p=0.97):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    monkeypatch.setenv("COACH_REPORT_ONLY_CONFIRM_CAP", "0")  # every confirm is capped
    spec = importlib.util.spec_from_file_location("coach_init_cont", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": verdict_p, "evidence": "e"}))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: (_ for _ in ()).throw(AssertionError("capped")))
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, tmp_path / "coach" / "log.jsonl"


def test_pre_verify_logs_guard_continued_run_for_a_continued_worker_turn(tmp_path, monkeypatch):
    hooks, logf = _hooks(tmp_path, monkeypatch)
    sid = "w1"
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="kanban")
    hooks["post_tool_call"](tool_name="tool_call", session_id=sid, result=json.dumps({"ok": True}),
                            args={"calls": [{"name": "kanban_continue", "arguments": {"checkpoint": "WIP pushed"}}]})
    assert hooks["pre_verify"](session_id=sid, platform="kanban", attempt=0, final_response=DRAFT) is None
    logged = [json.loads(l) for l in logf.read_text().splitlines()]
    assert any(r["event"] == "reply_checked" and r.get("guard") == "continued_run" for r in logged)
    assert not any(r["event"] == "reply_nudge" for r in logged)


def test_pre_verify_kanban_platform_capped_confirm_skips_the_screen_nudge(tmp_path, monkeypatch):
    hooks, logf = _hooks(tmp_path, monkeypatch)
    sid = "w2"
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="kanban")
    assert hooks["pre_verify"](session_id=sid, platform="kanban", attempt=0, final_response=DRAFT) is None
    logged = [json.loads(l) for l in logf.read_text().splitlines()]
    assert any(r["event"] == "report_only_scored" and r["source"] == "screen_capped_skip" for r in logged)
    # an interactive chat with the same capped confirm still nudges on a 0.97 screen
    hooks2, logf2 = _hooks(tmp_path / "b", monkeypatch)
    hooks2["pre_llm_call"](session_id="c1", user_message=ASK, platform="cli")
    out = hooks2["pre_verify"](session_id="c1", platform="cli", attempt=0, final_response=DRAFT)
    assert out and out["action"] == "continue"

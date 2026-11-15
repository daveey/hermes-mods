"""PR reads the coach's 140-char turn log could not see (VERIFY t_01a1d31ce47229ff, self-heal agent-36ec41dc12).

On the hub every chat terminal command reaches post_tool_call with kanban-worker-nice's ~115-char niceness preamble in
front of it (`# kanban-worker-nice terminal floor\\nif [ "$(nice)" -lt 10 ]; then renice ...; fi\\n`), so the 140-char
`-> terminal(...)` line kept ~25 chars of the real command and no read was ever visible:
A. pr_state_claim nudges fired on turns that had read the PR, and every outcome after a post-nudge `gh pr view` logged
   resolved=false (hub 2026-11-13 05:54-06:04Z: n_9b2c4f9b33504b9f, n_109a6b8a4b2f4800, n_338a7ee4f5364783).
B. A read past char 140 of a long command was invisible even without the preamble (n_ead1437cf4504565).
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# kanban-worker-nice's terminal_request prefix, as the hub's post_tool_call hook receives it
NICE = ('# kanban-worker-nice terminal floor\n'
        'if [ "$(nice)" -lt 10 ]; then renice -n 10 -p "${BASHPID:-$$}" >/dev/null || exit $?; fi\n')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hooks(tmp_path, monkeypatch):
    mod = load("coach_init_pr_read_outcome", ROOT / "__init__.py")
    c = mod._load("core")
    rcm = mod._load("reply_check")
    monkeypatch.setattr(rcm, "aux_judge", lambda s, p: json.dumps({k: 0 for k in rcm.RULES} | {"evidence": ""}))
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    monkeypatch.setenv("COACH_STALLED_GOAL", "0")
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    root = tmp_path / "coach"
    _, hooks = mod.build(store=c.Store(root=root), checkout=None)
    rows = lambda: [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]  # noqa: E731
    return hooks, rows, mod


SID = "20261111_090650_1247e7"
DRAFT = "One of my cards is still open: the card-placement fix (PR #1851)."
FINAL = ("The self-heal fixes are merged and live on the hub. One of my cards is still open: the card-placement fix "
         "([PR #1851](https://github.com/example/repo/pull/1851)), checked at 05:55Z.")
# n_338a7ee4f5364783's read, as the agent ran it after the nudge
READ = ('cd ~; token-broker exec --scope user/github --reason "PR1851 state" -- gh pr view 1851 -R example/repo '
        '--json state,mergeStateStatus --jq \'.state+"\\t"+.mergeStateStatus\'; date -u +%H:%M:%S')
# n_ead1437cf4504565's command: the read starts past char 140 even without the preamble
LONG = ('export PATH="$HOME/.local/bin:$PATH"; K=~/.hermes/kanban.db; SH=~/.hermes/kanban/sync-shadow/default.db; '
        'echo "== worker-5 label fix follow-ups: card rows then PR reads"\n'
        'token-broker exec --scope user/github --reason "PR reads" -- gh pr view 1891 -R example/repo '
        '--json state,mergedAt,reviewDecision')
OUT = '{"output": "OPEN\\t\\tUNKNOWN\\n05:55:01", "exit_code": 0}'


def _tool(hooks, command, sid=SID, call="toolu_01PR1851"):
    hooks["post_tool_call"](tool_name="terminal", args={"command": command}, result=OUT, session_id=sid,
                            tool_call_id=call, status="ok", duration_ms=2340, turn_id="t1", task_id="tk",
                            api_request_id="msg_1", middleware_trace=[], conversation_id="c1")


def _nudge(hooks, sid=SID, draft=DRAFT, ask="Kanban t_01a1d2a8ea done"):
    hooks["pre_llm_call"](session_id=sid, user_message=ask, platform="desktop")
    out = hooks["pre_verify"](session_id=sid, platform="desktop", attempt=0, final_response=draft)
    assert out and out["action"] == "continue" and "pr_state_claim" in out["message"]


def _outcome(rows):
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1, outs
    return outs[0]


def test_read_after_the_nudge_resolves_the_outcome(tmp_path, monkeypatch):
    hooks, rows, _ = _hooks(tmp_path, monkeypatch)
    _nudge(hooks)
    _tool(hooks, READ)
    assert hooks["pre_verify"](session_id=SID, platform="desktop", attempt=1, final_response=FINAL) is None
    o = _outcome(rows)
    assert o["rule"] == "pr_state_claim" and o["resolved"] is True, o


def test_read_behind_the_niceness_preamble_resolves_the_outcome(tmp_path, monkeypatch):
    """Defect A as it happened on the hub: the post-nudge read arrives with kanban-worker-nice's prefix."""
    hooks, rows, _ = _hooks(tmp_path, monkeypatch)
    _nudge(hooks)
    _tool(hooks, NICE + READ)
    assert hooks["pre_verify"](session_id=SID, platform="desktop", attempt=1, final_response=FINAL) is None
    o = _outcome(rows)
    assert o["rule"] == "pr_state_claim" and o["resolved"] is True, o


def test_read_behind_the_niceness_preamble_holds_no_nudge(tmp_path, monkeypatch):
    """The nudge side (n_2173e8ac61214719, n_109a6b8a4b2f4800): the turn read the PR before its reply."""
    hooks, rows, _ = _hooks(tmp_path, monkeypatch)
    hooks["pre_llm_call"](session_id=SID, user_message="is 1851 merged?", platform="desktop")
    _tool(hooks, NICE + READ)
    assert hooks["pre_verify"](session_id=SID, platform="desktop", attempt=0, final_response=FINAL) is None
    assert not [r for r in rows() if r["event"] == "reply_nudge"]


def test_read_past_char_140_holds_no_nudge(tmp_path, monkeypatch):
    """Defect B: `gh pr view` sits past char 140 of the command (no preamble)."""
    hooks, rows, _ = _hooks(tmp_path, monkeypatch)
    assert LONG.index("gh pr view") > 140
    hooks["pre_llm_call"](session_id=SID, user_message="status of the worker-5 fixes?", platform="desktop")
    _tool(hooks, LONG)
    reply = "The worker-5 label fix follow-ups (PR 1891) are approved and still open."
    assert hooks["pre_verify"](session_id=SID, platform="desktop", attempt=0, final_response=reply) is None
    assert not [r for r in rows() if r["event"] == "reply_nudge"]


def test_turn_log_line_drops_the_preamble_and_keeps_late_reads(tmp_path, monkeypatch):
    _, _, mod = _hooks(tmp_path, monkeypatch)
    ar = mod._load("agent_rules")
    coach = ar.AgentCoach(checkout=None)
    coach.turn_start("s", "go")
    coach.observe("s", "terminal", {"command": NICE + "git status"}, "{}", "ok")
    coach.observe("s", "terminal", {"command": LONG}, OUT, "ok")
    coach.observe("s", "terminal", {"command": "echo " + "x" * 200 + "; gh pr view 1 --help"}, OUT, "ok")
    log = coach.turn_context("s")[1]
    assert log[0] == "-> terminal(git status)", log
    assert any(l.startswith("   <- PR read: gh pr view 1891 -R example/repo") for l in log), log
    assert sum("PR read" in l for l in log) == 1, log  # --help is not a read
    rcm = mod._load("reply_check")
    # one read pattern: the line the coach writes is the line the rules look for
    assert ar.PR_READ_RE.pattern == rcm._PSC_READ_RE.pattern


# agent-017050f699: the read ran through a token-broker wrapper function, so no `gh pr view` text appeared at all
# (MacBook state.db msg 923525/923587, repo scrubbed). `G pr view` / `G api .../pulls/...` are the reads.
WRAPPED = ('TB=$HOME/.local/bin/token-broker; R=example/repo; G() { timeout 60 $TB exec --scope user/github --reason '
           '"PR blocker check 2748/2670" -- gh "$@" 2>&1 | grep -vE \'gh[ps]_|eyJ\'; }; '
           'TIP=$(G api repos/$R/branches/state --jq .commit.sha); for n in 2748 2670; do echo "=== #$n"; '
           'G pr view $n -R $R --json state,mergeStateStatus,mergeable --jq \'.state\'; G pr checks $n -R $R | wc -l; '
           'G api repos/$R/pulls/$n/reviews --jq \'.[-1].state\'; done; TZ=America/Los_Angeles date \'+%T\'')
WRAPPED_REPLY = ("**#2748 and #2670 are still unmerged, and the guard fix isn't the only step left** (checked 16:21 PT): "
                 "both are open.")


def test_read_through_a_gh_wrapper_function_holds_no_nudge(tmp_path, monkeypatch):
    hooks, rows, _ = _hooks(tmp_path, monkeypatch)
    hooks["pre_llm_call"](session_id=SID, user_message="are 2748 and 2670 merged?", platform="desktop")
    _tool(hooks, WRAPPED)
    assert hooks["pre_verify"](session_id=SID, platform="desktop", attempt=0, final_response=WRAPPED_REPLY) is None
    assert not [r for r in rows() if r["event"] == "reply_nudge"]


def test_wrapper_read_after_the_nudge_resolves_the_outcome(tmp_path, monkeypatch):
    hooks, rows, _ = _hooks(tmp_path, monkeypatch)
    _nudge(hooks)
    _tool(hooks, WRAPPED)
    assert hooks["pre_verify"](session_id=SID, platform="desktop", attempt=1, final_response=FINAL) is None
    o = _outcome(rows)
    assert o["rule"] == "pr_state_claim" and o["resolved"] is True, o


def test_pr_read_lines_expand_gh_wrappers(tmp_path, monkeypatch):
    _, _, mod = _hooks(tmp_path, monkeypatch)
    ar = mod._load("agent_rules")
    lines = ar.pr_read_lines(WRAPPED)
    assert any(l.startswith("   <- PR read: gh ") and "gh pr view $n -R $R" in l for l in lines), lines
    # short wrapper commands (under the 140-char cut) still get a line: the `->` line shows `G pr view`, not gh
    assert ar.pr_read_lines('G() { gh "$@"; }; G pr view 5 --json state'), "short function wrapper"
    assert ar.pr_read_lines('alias g="token-broker exec --scope user/github -- gh"; g pr checks 5')
    assert ar.pr_read_lines('GH="token-broker exec --scope user/github -- gh"; $GH pr view 5')
    assert ar.pr_read_lines('GH="token-broker exec -- gh"; ${GH} api repos/o/r/pulls/5')
    # not reads: a function that does not forward to gh, a wrapper never called for a PR, --help
    assert not ar.pr_read_lines('G() { git "$@"; }; G pr view 5')
    assert not ar.pr_read_lines('G() { gh "$@"; }; G repo view o/r')
    assert not ar.pr_read_lines('G() { gh "$@"; }; G pr view --help')


def test_the_marker_matches_kanban_worker_nice():
    nice = load("kwn_for_coach_test", ROOT.parent / "kanban-worker-nice" / "__init__.py")
    assert nice._MARKER == NICE.split("\n")[0] + "\n"

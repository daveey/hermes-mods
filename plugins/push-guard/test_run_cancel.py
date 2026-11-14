"""Hand-cancelling a GitHub Actions run is refused; the attempt-checked script is the only cancel path.

Why (self-heal agent-017301de5e): a chat's `gh run cancel` stopped attempt 3 of a macOS CI run that its owner card had
re-queued minutes earlier. The command's only check was "status is queued", so the stale read ("attempt 2") never
stopped it. `skills/devops/github-ci-wait/scripts/ci_run_cancel.py --expect-attempt N` re-reads status AND run_attempt
in the same call and refuses a changed attempt; this guard sends every other cancel there.
Run: python3 -m pytest -q -p no:cacheprovider test_run_cancel.py
"""
import pytest

from tests_guard import block, plugin

guard = plugin.guard


@pytest.fixture(autouse=True)
def no_push_guards(tmp_path, monkeypatch):
    # The cancel gate holds on every host, with or without a push-guards.json.
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(tmp_path / "absent.json"))


REFUSED = [
    "gh run cancel 123",
    "gh run cancel 43439854563 -R daveey/hermes",
    "S=$(gh run view 1 --json status -q .status); case \"$S\" in queued*) gh run cancel 1 -R o/r;; esac",
    "token-broker exec --scope user/github --reason x -- gh run cancel 9 -R o/r",
    "GH_TOKEN=x gh -R o/r run cancel 9",
    "gh api -X POST repos/o/r/actions/runs/9/cancel",
    "gh api --method POST repos/{owner}/{repo}/actions/runs/9/force-cancel",
    "gh api repos/o/r/actions/runs/9/cancel -X POST",
    "curl -X POST -H 'Authorization: token t' https://api.github.com/repos/o/r/actions/runs/9/cancel",
    "ssh hermes-hub 'gh run cancel 9 -R o/r'",
    "bash -lc 'gh run cancel 9'",
    "hub-admin 'gh run cancel 9 -R o/r'",
]

ALLOWED = [
    "python3 ~/code/hermes-mods/skills/devops/github-ci-wait/scripts/ci_run_cancel.py --run-id 9 --gh-repo o/r "
    "--expect-attempt 2",
    "token-broker exec --scope user/github --reason x -- python3 scripts/ci_run_cancel.py --run-id 9 "
    "--gh-repo o/r --expect-attempt 1 --reason superseded",
    "gh run view 9 -R o/r --json status,attempt",
    "gh run rerun 9 --failed",
    "gh run list -R o/r --status queued",
    "gh api repos/o/r/actions/runs/9",
    "grep -n 'gh run cancel' SKILL.md",
    "echo 'gh run cancel 9'",
    "rg 'actions/runs/9/cancel' logs/",
    "python3 scripts/ci_latch_sweep.py o/r",
    # A `|` inside quotes is a regex alternation, not a pipe (live false positive on the fix card's own grep).
    "git show o:SKILL.md | grep -nE 'ci_run_cancel|merged_since|gh run cancel|--state merged' | head",
    "git log --oneline --grep 'cancel|gh run cancel 9'",
    "rg \"a|gh run cancel 9 -R o/r\" logs/",
]

REFUSED += [
    # quote-aware splitting still sees real separators around quoted text
    "echo 'a|b' | gh run cancel 9",
    "grep -q 'x;y' f && gh run cancel 9 -R o/r",
    "echo \"a|b\"; gh run cancel 9",
    # an unbalanced quote falls back to plain splitting rather than hiding the cancel
    "echo it's; gh run cancel 9",
]


@pytest.mark.parametrize("cmd", REFUSED)
def test_hand_cancel_is_refused_naming_the_script(cmd):
    got = block("terminal", {"command": cmd})
    assert got and got["action"] == "block", cmd
    assert "ci_run_cancel.py" in got["message"] and "--expect-attempt" in got["message"]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_script_and_reads_pass(cmd):
    assert block("terminal", {"command": cmd}) is None, cmd


def test_execute_code_cancels_are_refused():
    for code in [
        'from hermes_tools import terminal\nterminal("gh run cancel 9 -R o/r")',
        'import subprocess\nsubprocess.run(["gh", "run", "cancel", "9", "-R", "o/r"])',
        'import subprocess\nsubprocess.run(["gh", "api", "-X", "POST", "repos/o/r/actions/runs/9/cancel"])',
        'import urllib.request as u\nu.urlopen(u.Request("https://api.github.com/repos/o/r/actions/runs/9/cancel", '
        'method="POST"))',
        'import asyncio\nasyncio.create_subprocess_exec("gh", "run", "cancel", "9")',
    ]:
        got = block("execute_code", {"code": code})
        assert got and got["action"] == "block", code
    assert block("execute_code", {"code": 'print("gh run cancel 9")'}) is None
    assert block("execute_code", {"code": 'terminal("grep -n \'gh run cancel\' SKILL.md")'}) is None
    assert block("execute_code", {"code": 'terminal("git show x | grep -nE \'a|gh run cancel\'")'}) is None
    assert block("execute_code", {"code": 'terminal("python3 scripts/ci_run_cancel.py --run-id 9 '
                                          '--gh-repo o/r --expect-attempt 1")'}) is None


def test_tool_call_wrapper_is_seen():
    got = block("tool_call", {"calls": [{"name": "terminal", "arguments": {"command": "gh run cancel 9"}}]})
    assert got and got["action"] == "block"


def test_python_unparseable_literal_fallback():
    got = block("execute_code", {"code": 'terminal("gh run cancel 9"'})
    assert got and got["action"] == "block"


def test_unclosed_quote_adjacent_separator_fallback():
    got = block("terminal", {"command": "echo it's;gh run cancel 9"})
    assert got and got["action"] == "block"


def test_escaped_semicolon_not_command():
    assert block("terminal", {"command": r"echo a\;gh run cancel 9"}) is None


def test_escaped_double_quote_keeps_alternation_quoted():
    assert block("terminal", {"command": r'rg "a\"|gh run cancel 9" logs'}) is None


def test_adjacent_semicolon_real_cancel_blocked():
    got = block("terminal", {"command": "echo x;gh run cancel 9"})
    assert got and got["action"] == "block"


def test_override_is_logged_escape_hatch():
    assert block("terminal", {"command": "CI_CANCEL_GUARD=off gh run cancel 9"}) is None

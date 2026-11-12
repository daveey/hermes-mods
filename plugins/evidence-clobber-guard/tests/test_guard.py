"""Regression tests for evidence-clobber-guard (self-heal agent-a86164acd8).

A mutation run wrote ``> mutation-v2.log`` onto the previous run's log under
``~/.hermes/kanban/evidence/`` and destroyed it. These tests drive the real
registered ``pre_tool_call`` hook. Run:
    python3 -m pytest -q -p no:cacheprovider plugins/evidence-clobber-guard/tests
"""

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _load_hook():
    key = "evidence_clobber_guard_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(key, PLUGIN / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    hooks = {}

    class Ctx:
        def register_hook(self, name, fn):
            hooks[name] = fn

    mod.register(Ctx())
    assert "pre_tool_call" in hooks, "plugin registers no pre_tool_call hook"
    return hooks["pre_tool_call"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    ev = home / "kanban" / "evidence" / "t_demo"
    ev.mkdir(parents=True)
    (ev / "mutation-v2.log").write_text("run1: 92 killed, 30 survived, 1 timeout\n")
    other = tmp_path / "scratch"
    other.mkdir()
    (other / "out.log").write_text("not evidence\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_EVIDENCE_CLOBBER_GUARD", raising=False)
    return {"ev": ev, "other": other, "home": home, "hook": _load_hook()}


def run(env, command, workdir=None, tool="terminal"):
    args = {"command": command}
    if workdir:
        args["workdir"] = str(workdir)
    return env["hook"](tool_name=tool, args=args)


def assert_blocked(res, cmd):
    assert res and res.get("action") == "block", f"not blocked: {cmd!r}"
    assert "evidence file exists" in res["message"]
    assert ">>" in res["message"] and "-run<N>" in res["message"]


# The three shapes from the incident and the plan.
@pytest.mark.parametrize("tmpl", [
    "python3 mutate.py > {f}",
    "python3 mutate.py 2>&1 | tee {f}",
    "(python3 mutate.py; echo rc=$?) > {f}",
    "python3 mutate.py &> {f}",
    "python3 mutate.py >| {f}",
    "python3 mutate.py 2> {f}",
    "python3 mutate.py | tee -i {f}",
    "cd /tmp && python3 mutate.py > {f} 2>&1",
    "bash -c 'python3 mutate.py > {f}'",
])
def test_blocks_truncating_write_to_existing_evidence(env, tmpl):
    cmd = tmpl.format(f=env["ev"] / "mutation-v2.log")
    assert_blocked(run(env, cmd), cmd)


def test_blocks_relative_and_tilde_paths(env):
    assert_blocked(run(env, "python3 mutate.py > mutation-v2.log", workdir=env["ev"]), "relative")
    assert_blocked(run(env, f"cd {env['ev']} && python3 mutate.py > mutation-v2.log"), "cd-relative")
    tilde = "~/hermes/kanban/evidence/t_demo/mutation-v2.log"
    assert_blocked(run(env, f"echo x > {tilde}"), tilde)
    assert_blocked(run(env, "echo x > $HERMES_HOME/kanban/evidence/t_demo/mutation-v2.log"), "$HERMES_HOME")


def test_blocks_through_nested_tool_call(env):
    cmd = f"echo x > {env['ev'] / 'mutation-v2.log'}"
    res = env["hook"](tool_name="tool_call",
                      args={"calls": [{"name": "terminal", "arguments": {"command": cmd}}]})
    assert_blocked(res, cmd)


@pytest.mark.parametrize("tmpl", [
    "python3 mutate.py >> {f}",
    "python3 mutate.py &>> {f}",
    "python3 mutate.py 2>&1 | tee -a {f}",
    "python3 mutate.py | tee --append {f}",
    "cat {f}",
    "grep killed {f} > /dev/null",
    "echo 'see > {f}'",
    "git commit -m 'do not write > {f}'",
])
def test_allows_append_and_reads(env, tmpl):
    cmd = tmpl.format(f=env["ev"] / "mutation-v2.log")
    assert run(env, cmd) is None, cmd


def test_allows_new_evidence_path(env):
    new = env["ev"] / "mutation-run2-abcd1234.log"
    assert run(env, f"python3 mutate.py > {new} 2>&1") is None
    assert run(env, f"python3 mutate.py | tee {new}") is None


def test_allows_existing_file_outside_evidence(env):
    f = env["other"] / "out.log"
    assert run(env, f"python3 mutate.py > {f}") is None
    assert run(env, f"echo x | tee {f}") is None
    assert run(env, "echo x > /dev/null") is None


def test_ignores_other_tools_and_kill_switch(env, monkeypatch):
    f = env["ev"] / "mutation-v2.log"
    assert env["hook"](tool_name="read_file", args={"path": str(f)}) is None
    monkeypatch.setenv("HERMES_EVIDENCE_CLOBBER_GUARD", "off")
    assert run(env, f"echo x > {f}") is None


def test_refusal_is_logged(env):
    f = env["ev"] / "mutation-v2.log"
    assert_blocked(run(env, f"echo x > {f}"), "log")
    log = env["home"] / "logs" / "evidence-clobber-guard.log"
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[-1]["target"] == str(f) and rows[-1]["tool"] == "terminal"


# The fd of a redirect is read from the raw text (shlex drops whitespace): `2>x` is a redirect of fd 2, while a
# spaced `tee 2 > x` names the file `2`. The old `argv[-1].isdigit()` pop dropped that tee operand.
def test_fd_prefix_versus_spaced_digit_operand(env):
    (env["ev"] / "2").write_text("run1\n")
    for cmd in ("python3 mutate.py 2>/dev/null", "python3 mutate.py 2>&1 | tee -a mutation-v2.log",
                "( python3 mutate.py 2>&1 ) >> mutation-v2.log", "tee -a 2 2>/dev/null"):
        assert run(env, cmd, env["ev"]) is None, cmd
    for cmd in ("echo x | tee 2 > /dev/null", "python3 mutate.py 2>mutation-v2.log",
                "( python3 mutate.py 2>&1 ) > mutation-v2.log", "echo x | tee mutation-v2.log 2>/dev/null"):
        assert_blocked(run(env, cmd, env["ev"]), cmd)


# A quoted `<`/`>` is an argument, not a redirect: `grep -c '>' mutation-v2.log` was refused as a truncation of the
# evidence file, and `echo '>' > mutation-v2.log` wrote to a phantom file `>` and passed. An input dup has no target.
def test_quoted_redirect_and_input_dup(env):
    for cmd in ("grep -c '>' mutation-v2.log", 'grep -rn ">" .', "grep -c \\> mutation-v2.log",
                "python3 mutate.py 2<&1 | tee -a mutation-v2.log", "python3 mutate.py <&- >> mutation-v2.log"):
        assert run(env, cmd, env["ev"]) is None, cmd
    for cmd in ("echo '>' > mutation-v2.log", 'echo ">>" > mutation-v2.log', "echo x | tee '>' mutation-v2.log",
                "python3 mutate.py 2<&1 > mutation-v2.log"):
        assert_blocked(run(env, cmd, env["ev"]), cmd)


# Bash runs a command substitution inside double quotes (and in backticks) like an unquoted one, but shlex kept
# `"$(...)"` as one data word: `out="$(cmd > mutation-v2.log 2>&1)"` truncated the evidence file unchecked.
def test_double_quoted_substitution_runs_like_unquoted(env):
    for cmd in ("y=$(python3 mutate.py > mutation-v2.log)", 'echo "$(python3 mutate.py > mutation-v2.log)"',
                'out="$(python3 mutate.py > mutation-v2.log 2>&1)"', 'echo "`python3 mutate.py > mutation-v2.log`"',
                "echo `python3 mutate.py > mutation-v2.log`", 'echo "$(python3 mutate.py | tee mutation-v2.log)"'):
        assert_blocked(run(env, cmd, env["ev"]), cmd)
    for cmd in ("echo '$(python3 mutate.py > mutation-v2.log)'", 'echo "\\$(python3 mutate.py > mutation-v2.log)"',
                'echo "\\`python3 mutate.py > mutation-v2.log\\`"', 'out="$(python3 mutate.py >> mutation-v2.log)"',
                'out="$(git status 2>&1)"', 'n="$(grep -c ">" mutation-v2.log)"'):
        assert run(env, cmd, env["ev"]) is None, cmd


# Heredoc bodies and `#` comments are data (card t_01a1d020a0b79528). An apostrophe in either (`it's`, `don't`)
# unbalanced the quotes, so the lexer gave up on the whole command and a command AFTER it ran unchecked; the
# `$(...)` of an unquoted-delimiter body runs too, even beside an apostrophe. Shapes 1-7 are the card's probe.
HEREDOC_SHAPES = [
    "{c}",
    "cat > /tmp/n.txt <<EOF\nall done\nEOF\n{c}",
    "cat > /tmp/n.txt <<EOF\nit's done\nEOF\n{c}",
    "cat > /tmp/n.txt <<'EOF'\nit's done\nEOF\n{c}",
    "# don't skip this\n{c}",
    "cat > /tmp/n.txt <<EOF\nok $({c})\nEOF",
    "cat > /tmp/n.txt <<EOF\nit's $({c})\nEOF",
    "cat > /tmp/n.txt <<-EOF\n\tit's done\n\tEOF\n{c}",
    "cat > /tmp/a <<A; cat > /tmp/b <<'B'\nit's a\nA\nit's b\nB\n{c}",
    "cat > /tmp/n.txt <<\\EOF\nit's\nEOF\n{c}",
    "cat > /tmp/n.txt <<\"EOF\"\nit's\nEOF\n{c}",
    "cat > /tmp/n.txt <<EOF\nit's `{c}`\nEOF",
    "echo hi # it's a note\n{c}",
    "{c}\necho don't",
    "bash <<'EOF'\n# it's here\n{c}\nEOF",
]
HEREDOC_ALLOWED = [
    "cat > /tmp/n.txt <<EOF\nit's done\nEOF\n{safe}",
    "# don't worry\n{safe}",
    "cat > /tmp/n.txt <<EOF\nit's $(date)\nEOF",
    "cat > /tmp/n.txt <<'EOF'\nit's $({c})\nEOF",
    "cat > /tmp/n.txt <<EOF\nit's \\$({c})\nEOF",
]


def test_heredoc_and_comment_apostrophes_hide_no_write(env):
    for shape in HEREDOC_SHAPES:
        cmd = shape.format(c="python3 mutate.py > mutation-v2.log")
        assert_blocked(run(env, cmd, env["ev"]), cmd)
    for shape in HEREDOC_ALLOWED:
        cmd = shape.format(c="python3 mutate.py > mutation-v2.log", safe="ls plugins")
        assert run(env, cmd, env["ev"]) is None, cmd

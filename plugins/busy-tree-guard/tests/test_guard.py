"""Regression tests for busy-tree-guard (self-heal agent-10166983a7).

An agent started a 20-minute suite in the background (``t.sh $PWD/fix2``), then patched
files in ``fix2`` while the suite still read them; the suite's "1195 OK" matched no revision.
These tests drive the real registered ``pre_tool_call`` hook. Run:
    python3 -m pytest -q -p no:cacheprovider plugins/busy-tree-guard/tests
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
TASK = "task-under-test"


class FakeRegistry:
    """Shape of tools.process_registry.process_registry: list_sessions() + get()."""

    def __init__(self):
        self.procs = []

    def add(self, command, cwd, *, task=TASK, running=True):
        sid = "proc_" + uuid.uuid4().hex[:12]
        self.procs.append({"session_id": sid, "command": command, "cwd": str(cwd), "pid": 4242,
                           "owner_task_id": task, "started_at": "2026-11-06T03:23:00",
                           "status": "running" if running else "exited"})
        return sid

    def list_sessions(self, task_id=None, session_key=None, **_):
        return [dict(p, command=p["command"][:200]) for p in self.procs
                if task_id is None or p["owner_task_id"] == task_id]

    def get(self, sid):
        for p in self.procs:
            if p["session_id"] == sid:
                return type("S", (), {"command": p["command"]})()
        return None


def _load():
    key = "busy_tree_guard_" + uuid.uuid4().hex
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
    return mod, hooks["pre_tool_call"]


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "retention_policy.py").write_text("KEEP = 1\n")
    (path / "tests").mkdir()
    (path / "tests" / "test_retention.py").write_text("def test_x():\n    pass\n")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("BUSY_TREE_GUARD", raising=False)
    mod, hook = _load()
    reg = FakeRegistry()
    mod.guard_registry_override(reg)
    trees = {n: _repo(tmp_path / "work" / n) for n in ("fix2", "other")}
    return {"mod": mod, "hook": hook, "reg": reg, "home": home, "tmp": tmp_path, **trees}


def _patch(path):
    return {"path": str(path), "old_string": "KEEP = 1", "new_string": "KEEP = 2"}


def test_background_unittest_blocks_patch_in_its_tree(env):
    env["reg"].add("sleep 90; python3 -m unittest discover", env["fix2"])
    res = env["hook"](tool_name="patch", args=_patch(env["fix2"] / "retention_policy.py"), task_id=TASK)
    assert res and res["action"] == "block", res
    msg = res["message"]
    assert "busy-tree-guard" in msg and str(env["fix2"]) in msg
    assert "python3 -m unittest" in msg and "2026-11-06T03:23:00" in msg
    assert "worktree" in msg and "kill" in msg.lower()
    log = (env["home"] / "logs" / "busy-tree-guard.log").read_text().splitlines()
    row = json.loads(log[-1])
    assert row["tool"] == "patch" and row["tree"] == str(env["fix2"])


def test_incident_shape_t_sh_dollar_pwd_from_parent_cwd_blocks_fix2(env):
    # t.sh $PWD/fix2 started from the parent dir: the process cwd is NOT the tree.
    parent = env["fix2"].parent
    env["reg"].add("bash ~/bin/t.sh $PWD/fix2 > /tmp/run.log 2>&1", parent)
    for tool, args in (
        ("patch", _patch(env["fix2"] / "retention_policy.py")),
        ("write_file", {"path": str(env["fix2"] / "tests" / "test_retention.py"), "content": "x"}),
        ("mcp__patch", _patch(env["fix2"] / "retention_policy.py")),
        ("patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: "
                   + str(env["fix2"] / "retention_policy.py") + "\n@@\n-KEEP = 1\n+KEEP = 2\n*** End Patch"}),
        ("terminal", {"command": f"sed -i s/1/2/ {env['fix2']}/retention_policy.py"}),
        ("terminal", {"command": f"cd {env['fix2']} && git stash"}),
        ("terminal", {"command": "git checkout -- retention_policy.py", "workdir": str(env["fix2"])}),
        ("terminal", {"command": f"echo X > {env['fix2']}/retention_policy.py"}),
        ("tool_call", {"calls": [{"name": "patch", "arguments": _patch(env["fix2"] / "retention_policy.py")}]}),
    ):
        res = env["hook"](tool_name=tool, args=args, task_id=TASK)
        assert res and res["action"] == "block", (tool, args, res)


def test_allowed_cases(env):
    reg = env["reg"]
    reg.add("python3 -m pytest -q", env["fix2"])                               # busy: fix2
    reg.add("python3 -m pytest -q", env["other"], running=False)                # exited
    reg.add("python3 -m http.server 8000", env["other"])                       # not a test
    reg.add("python3 -m pytest -q", env["other"], task="someone-else")          # other session
    allowed = [
        ("patch", _patch(env["other"] / "retention_policy.py")),                 # another tree
        ("write_file", {"path": str(env["tmp"] / "notes.txt"), "content": "x"}),  # outside any repo
        ("terminal", {"command": f"cat {env['fix2']}/retention_policy.py"}),      # read-only
        ("terminal", {"command": f"git -C {env['fix2']} status && git -C {env['fix2']} log -1"}),
        ("terminal", {"command": f"python3 -m pytest {env['fix2']} > /tmp/x.log"}),
        ("terminal", {"command": f"sed -i s/1/2/ {env['other']}/retention_policy.py"}),
    ]
    for tool, args in allowed:
        assert env["hook"](tool_name=tool, args=args, task_id=TASK) is None, (tool, args)


def test_kill_switch_and_exit_release_the_tree(env, monkeypatch):
    sid = env["reg"].add("python3 -m unittest", env["fix2"])
    args = _patch(env["fix2"] / "retention_policy.py")
    assert env["hook"](tool_name="patch", args=args, task_id=TASK)
    monkeypatch.setenv("BUSY_TREE_GUARD", "0")
    assert env["hook"](tool_name="patch", args=args, task_id=TASK) is None
    monkeypatch.delenv("BUSY_TREE_GUARD")
    for p in env["reg"].procs:
        if p["session_id"] == sid:
            p["status"] = "exited"
    assert env["hook"](tool_name="patch", args=args, task_id=TASK) is None


def test_interpreter_path_does_not_make_its_repo_busy(env):
    # A venv python inside another checkout is the runner, not the tree under test.
    venv_py = env["other"] / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    env["reg"].add(f"{venv_py} -m pytest -q", env["fix2"])
    assert env["hook"](tool_name="patch", args=_patch(env["other"] / "retention_policy.py"),
                       task_id=TASK) is None
    assert env["hook"](tool_name="patch", args=_patch(env["fix2"] / "retention_policy.py"), task_id=TASK)


@pytest.mark.parametrize("redirect", ["2>&1", "1>&2", "2>&-", "2>&1-"])
def test_fd_redirections_are_not_file_writes(env, redirect):
    env["reg"].add("python -m pytest", env["fix2"])
    assert env["hook"](tool_name="terminal", args={
        "workdir": str(env["fix2"]),
        "command": f"timeout 400 python -m pytest -q -p no:cacheprovider {redirect} | tail -3",
    }, task_id=TASK) is None


def test_explicit_cd_does_not_mark_launch_tree_busy(env):
    env["reg"].add(f"cd {env['fix2']} && python -m pytest", env["other"])
    assert env["hook"](tool_name="patch", args=_patch(env["other"] / "retention_policy.py"), task_id=TASK) is None
    assert env["hook"](tool_name="patch", args=_patch(env["fix2"] / "retention_policy.py"), task_id=TASK)


@pytest.mark.parametrize("redirect", [">1", "2>errors", ">&errors", "&>errors", ">>errors", ">errors 2>&1"])
def test_real_file_redirections_still_block(env, redirect):
    env["reg"].add("python -m pytest", env["fix2"])
    assert env["hook"](tool_name="terminal", args={
        "workdir": str(env["fix2"]), "command": f"python -m pytest {redirect}"
    }, task_id=TASK)


# The fd of a redirect is read from the raw text (shlex drops whitespace): a digit glued to `>` is the fd, a spaced
# digit is an operand. The old `argv[-1].isdigit()` pop also dropped the operand of `rm 2 > /dev/null`.
def test_fd_prefix_versus_spaced_digit_operand(env):
    env["reg"].add("python -m pytest", env["fix2"])
    for cmd in ("rm -f /tmp/x 2>/dev/null", "rm -f /tmp/x 2>&1", "( rm -f /tmp/x 2>/dev/null )",
                "touch /tmp/x 1>/dev/null 2>|/dev/null", "rm -f /tmp/x &>/dev/null"):
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["fix2"]), "command": cmd},
                           task_id=TASK) is None, cmd
    for cmd in ("rm -f 2 > /dev/null", "rm -f /tmp/x 2>errors", "( rm -f retention_policy.py 2>/dev/null )"):
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["fix2"]), "command": cmd},
                           task_id=TASK), cmd


# A quoted `<`/`>` is an argument, not a redirect: `grep -c '>' f` in a busy tree read `f` as a write, and
# `echo '>' > <tree>/f` from elsewhere wrote `<cwd>/>` instead of `<tree>/f`. An input dup has no file operand.
def test_quoted_redirect_and_input_dup(env):
    env["reg"].add("python -m pytest", env["fix2"])
    for cmd in ("grep -c '>' retention_policy.py", 'grep -rn ">>" .', "grep -c \\> retention_policy.py",
                "mkdir -p /tmp/x 2<&1", "touch /tmp/x <&-", "cat retention_policy.py 2<&1 | grep -c '<'"):
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["fix2"]), "command": cmd},
                           task_id=TASK) is None, cmd
    for cmd in (f"echo '>' > {env['fix2']}/retention_policy.py", f"echo \">>\" >> {env['fix2']}/retention_policy.py",
                f"rm -f {env['fix2']}/retention_policy.py 2<&1"):
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["other"]), "command": cmd},
                           task_id=TASK), cmd


def test_cd_resolves_pwd_test_argument(env):
    child = _repo(env["other"] / "nested")
    env["reg"].add(f"cd {env['other']} && bash t.sh $PWD/nested", env["fix2"])
    assert env["hook"](tool_name="patch", args=_patch(child / "retention_policy.py"), task_id=TASK)
    assert env["hook"](tool_name="patch", args=_patch(env["fix2"] / "retention_policy.py"), task_id=TASK) is None


def test_sibling_path_containment(env):
    guard = env["mod"].guard
    assert not guard._inside(str(env["fix2"]) + "-sibling/file", str(env["fix2"]))


def test_override_message_explains_parent_environment(env):
    env["reg"].add("python -m pytest", env["fix2"])
    result = env["hook"](tool_name="terminal", args={
        "command": "BUSY_TREE_GUARD=0 touch retention_policy.py", "workdir": str(env["fix2"])
    }, task_id=TASK)
    assert result and "before starting Hermes" in result["message"]
    assert "inline" in result["message"]


CORE = Path(os.environ.get("HERMES_CORE", Path.home() / ".hermes" / "hermes-agent"))


@pytest.mark.skipif(not (CORE / "tools" / "process_registry.py").exists(), reason="needs Hermes core")
def test_real_process_registry_spawn(tmp_path, monkeypatch):
    """Same path as the incident: a background terminal process in the real registry."""
    monkeypatch.syspath_prepend(str(CORE))
    try:
        from tools.process_registry import ProcessRegistry
    except Exception as exc:  # core deps missing in this interpreter
        pytest.skip(f"core not importable: {exc}")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    mod, hook = _load()
    reg = ProcessRegistry()
    mod.guard_registry_override(reg)
    tree = _repo(tmp_path / "fix2")
    task = "real-" + uuid.uuid4().hex[:6]
    s = reg.spawn_local("sleep 30; python3 -m unittest", cwd=str(tree), task_id=task)
    try:
        time.sleep(0.3)
        res = hook(tool_name="patch", args=_patch(tree / "retention_policy.py"), task_id=task)
        assert res and res["action"] == "block", res
        assert s.id in res["message"]
    finally:
        reg.kill_process(s.id)
    deadline = time.time() + 10
    while time.time() < deadline and hook(tool_name="patch", args=_patch(tree / "retention_policy.py"),
                                          task_id=task):
        time.sleep(0.2)
    assert hook(tool_name="patch", args=_patch(tree / "retention_policy.py"), task_id=task) is None


# Bash runs a command substitution inside double quotes (and in backticks) like an unquoted one, but shlex kept
# `"$(...)"` as one data word: `echo "$(rm -rf plugins/x)"` named no target while `echo $(rm -rf plugins/x)` did.
def _guard_module():
    spec = importlib.util.spec_from_file_location("busy_tree_guard_decisions_" + uuid.uuid4().hex,
                                                  PLUGIN / "guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_double_quoted_substitution_targets(tmp_path):
    g = _guard_module()
    cwd = str(tmp_path)
    want = [str(tmp_path / "plugins" / "x")]
    for cmd in ("rm -rf plugins/x", "echo $(rm -rf plugins/x)", 'echo "$(rm -rf plugins/x)"',
                'out="$(rm -rf plugins/x 2>&1)"', 'echo "`rm -rf plugins/x`"', "echo `rm -rf plugins/x`",
                'echo "$(echo hi > plugins/x)"', 'echo "a $(echo "b c" | tee plugins/x) d"'):
        assert g.shell_targets(cmd, cwd) == want, cmd
    for cmd in ("echo '$(rm -rf plugins/x)'", 'echo "\\$(rm -rf plugins/x)"', 'echo "\\`rm -rf plugins/x\\`"',
                'out="$(git status 2>&1)"', 'out="$(ls plugins 2>/dev/null)"', 'echo "$((3 > 2))"'):
        assert g.shell_targets(cmd, cwd) == [], cmd


def test_double_quoted_substitution_refused_in_busy_tree(env):
    env["reg"].add("python -m pytest", env["fix2"])
    for cmd in ('echo "$(rm -f retention_policy.py)"', 'out="$(rm -f retention_policy.py 2>&1)"',
                'echo "`rm -f retention_policy.py`"'):
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["fix2"]), "command": cmd},
                           task_id=TASK), cmd
    for cmd in ("echo '$(rm -f retention_policy.py)'", 'echo "\\$(rm -f retention_policy.py)"',
                'out="$(git status 2>&1)"'):
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["fix2"]), "command": cmd},
                           task_id=TASK) is None, cmd


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


@pytest.mark.parametrize("shape", HEREDOC_SHAPES)
def test_heredoc_and_comment_apostrophes_hide_no_target(tmp_path, shape):
    g = _guard_module()
    cmd = shape.format(c="rm -rf plugins/x")
    assert str(tmp_path / "plugins" / "x") in g.shell_targets(cmd, str(tmp_path)), cmd


@pytest.mark.parametrize("shape", HEREDOC_ALLOWED)
def test_heredoc_and_comment_data_targets_nothing_in_the_tree(tmp_path, shape):
    g = _guard_module()
    cmd = shape.format(c="rm -rf plugins/x", safe="ls plugins")
    # `cat > /tmp/n.txt` is a real write outside the tree; nothing inside it may be named
    assert [t for t in g.shell_targets(cmd, str(tmp_path)) if t.startswith(str(tmp_path))] == [], cmd


def test_heredoc_and_comment_apostrophes_refused_in_busy_tree(env):
    env["reg"].add("python -m pytest", env["fix2"])
    for shape in HEREDOC_SHAPES:
        cmd = shape.format(c="rm -f retention_policy.py")
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["fix2"]), "command": cmd},
                           task_id=TASK), cmd
    for shape in HEREDOC_ALLOWED:
        cmd = shape.format(c="rm -f retention_policy.py", safe="ls")
        assert env["hook"](tool_name="terminal", args={"workdir": str(env["fix2"]), "command": cmd},
                           task_id=TASK) is None, cmd


# A heredoc body is data only when every reader is known to read it as data (cat, tee, grep, git, python, ...).
# A body fed to anything else may run as shell code: `su -`, `sudo -s`/`-i`, `source /dev/stdin`, `"$SHELL"`,
# `eval`, `exec bash`, `xargs sh`, a pipe into `sudo -s`, or an unknown reader. Review of 77ac105f found those
# read as data (fail open); they are code again (card t_01a1d020a0b79528). `<<` inside `${...}` is no heredoc.
# Review of df783b71: a body read inside a command substitution is data only when the substitution's output
# goes to a data consumer (git/gh args, an assignment); `bash -c "$(cat <<'EOF' ...)"`, `eval "$(cat ...)"`
# and a reader on a line with `>(bash)` run it. So does a script written from a body and then run (`bash s.sh`).
READER_SHAPES = [
    "su - <<EOF\n{c}\nEOF",
    "sudo -s <<EOF\n{c}\nEOF",
    "sudo -i <<'EOF'\necho here\n{c}\nEOF",
    "sudo --login <<EOF\n{c}\nEOF",
    "sudo -u root bash <<'EOF'\n{c}\nEOF",
    "source /dev/stdin <<EOF\n{c}\nEOF",
    ". /dev/stdin <<'EOF'\n{c}\nEOF",
    "\"$SHELL\" <<'EOF'\n{c}\nEOF",
    "${{SHELL}} <<EOF\n{c}\nEOF",
    "eval \"$(cat)\" <<'EOF'\n{c}\nEOF",
    "exec bash <<'EOF'\n{c}\nEOF",
    "xargs -0 sh -c <<'EOF'\n{c}\nEOF",
    "cat <<'EOF' | sudo -s\n{c}\nEOF",
    "cat <<'EOF' | su\n{c}\nEOF",
    "cat <<'EOF' | xargs sh -c\n{c}\nEOF",
    "some-tool <<'EOF'\n{c}\nEOF",
    "echo ${{x/<</y}}\n{c}",
    "bash -c \"$(cat <<'EOF'\n{c}\nEOF\n)\"",
    "eval \"$(cat <<'EOF'\n{c}\nEOF\n)\"",
    "sh -c \"$(cat <<EOF\n{c}\nEOF\n)\"",
    "bash -c \"$(\ncat <<'EOF'\n{c}\nEOF\n)\"",
    "eval `cat <<'EOF'\n{c}\nEOF\n`",
    "python3 -c \"$(cat <<'EOF'\n{c}\nEOF\n)\"",
    "git commit -m \"$(echo \"$(cat <<'EOF'\n{c}\nEOF\n)\")\"",
    "bash <(cat <<'EOF'\n{c}\nEOF\n)",
    "tee >(bash) <<'EOF'\n{c}\nEOF",
    "cat <<'EOF' > >(bash)\n{c}\nEOF",
    "cat > /tmp/s.sh <<'EOF'\n{c}\nEOF\nbash /tmp/s.sh",
    "cat > s.sh <<'EOF'\n{c}\nEOF\nchmod +x s.sh && ./s.sh",
    "tee /tmp/x.sh >/dev/null <<'EOF'\n{c}\nEOF\nsource /tmp/x.sh",
]
READER_ALLOWED = [
    "sudo tee /tmp/n.conf <<'EOF'\nit's {c}\nEOF",
    "cat <<'EOF' | grep it\nit's {c}\nEOF",
    "python3 - <<'EOF'\nprint(\"it's {c}\")\nEOF",
    "env FOO=1 cat > /tmp/n.txt 2>&1 <<'EOF'\nit's {c}\nEOF",
    "gh issue create --body-file - <<'EOF'\nit's {c}\nEOF",
    "gh pr create --title t --body \"$(cat <<'EOF'\nit's {c}\nEOF\n)\"",
    "x=$(cat <<'EOF'\nit's {c}\nEOF\n)",
    "export MSG=\"$(cat <<'EOF'\nit's {c}\nEOF\n)\"",
    "cat > /tmp/s.sh <<'EOF'\nit's {c}\nEOF\nbash /tmp/other.sh",
]

GIT_COMMIT_MESSAGES = [
    "git commit -m \"$(cat <<'EOF'\nwe don't {c}\nEOF\n)\"",
    "git commit -m \"$(date)\" -m \"$(cat <<'EOF'\nwe don't {c}\nEOF\n)\"",
]


@pytest.mark.parametrize("shape", READER_SHAPES)
def test_heredoc_body_a_shell_may_read_names_its_target(tmp_path, shape):
    g = _guard_module()
    cmd = shape.format(c="rm -rf plugins/x")
    assert str(tmp_path / "plugins" / "x") in g.shell_targets(cmd, str(tmp_path)), cmd


@pytest.mark.parametrize("shape", READER_ALLOWED)
def test_heredoc_body_a_data_reader_reads_targets_nothing_in_the_tree(tmp_path, shape):
    g = _guard_module()
    cmd = shape.format(c="rm -rf plugins/x")
    assert [t for t in g.shell_targets(cmd, str(tmp_path)) if t.startswith(str(tmp_path))] == [], cmd


# `git commit` writes the tree itself, so this guard names the tree root; the message body's CMD is never a target.
@pytest.mark.parametrize("shape", GIT_COMMIT_MESSAGES)
def test_heredoc_commit_message_body_is_not_a_target(tmp_path, shape):
    g = _guard_module()
    cmd = shape.format(c="rm -rf plugins/x")
    assert [t for t in g.shell_targets(cmd, str(tmp_path)) if t.startswith(str(tmp_path))] == [str(tmp_path)], cmd


# Store data first, then run it in the same tool call. Test actual admission, not extraction.
STORED_ASSIGNMENT = "x=$(cat <<'EOF'\n{c}\nEOF\n)\n"
STORED_FILE = "cat > /tmp/stored-heredoc-regression.sh <<'EOF'\n{c}\nEOF\n"
STORED_RUNS = [
    pytest.param(STORED_ASSIGNMENT + 'eval "$x"', id="assignment-eval"),
    pytest.param(STORED_ASSIGNMENT + 'bash -c "$x"', id="assignment-bash-c"),
    pytest.param(STORED_ASSIGNMENT + '$x', id="assignment-command"),
    pytest.param(STORED_ASSIGNMENT + 'source <(printf "%s\\n" "$x")', id="assignment-source"),
    pytest.param(STORED_FILE + 'bash < /tmp/stored-heredoc-regression.sh', id="file-shell-stdin"),
    pytest.param(STORED_FILE + '< /tmp/stored-heredoc-regression.sh bash', id="file-redirect-first"),
    pytest.param(STORED_FILE + '0< /tmp/stored-heredoc-regression.sh bash', id="file-fd-first"),
    pytest.param(STORED_FILE + 'bash 0< /tmp/stored-heredoc-regression.sh', id="file-fd-stdin"),
    pytest.param(STORED_FILE + 'bash -x 0</tmp/stored-heredoc-regression.sh', id="file-fd-options"),
    pytest.param(STORED_FILE + 'cat /tmp/stored-heredoc-regression.sh | bash', id="file-pipe-shell"),
    pytest.param(STORED_FILE + 'sh -c "$(cat /tmp/stored-heredoc-regression.sh)"', id="file-shell-cat"),
    pytest.param(STORED_FILE + 'eval "$(< /tmp/stored-heredoc-regression.sh)"', id="file-eval-read"),
]
STORED_CONTROLS = [
    pytest.param(STORED_ASSIGNMENT, id="assignment-no-run"),
    pytest.param('git commit -m "$(cat <<\'EOF\'\n{c}\nEOF\n)"', id="commit-message"),
    pytest.param(STORED_FILE + 'cat /tmp/stored-heredoc-regression.sh', id="write-then-cat"),
    pytest.param(STORED_FILE + 'wc -l /tmp/stored-heredoc-regression.sh', id="write-then-wc"),
    pytest.param(STORED_FILE + 'bash < /tmp/other-file', id="other-stdin"),
    pytest.param(STORED_FILE + 'cat /tmp/other-file | bash', id="other-pipe"),
    pytest.param(STORED_FILE + '< /tmp/other-file bash', id="other-redirect-first"),
    pytest.param(STORED_FILE + 'bash 0< /tmp/other-file', id="other-fd"),
    pytest.param(STORED_ASSIGNMENT + "echo 'eval \"$x\"; bash -c \"$x\"; $x'", id="quoted-variable-references"),
    pytest.param(STORED_FILE + "echo 'bash < /tmp/stored-heredoc-regression.sh; cat /tmp/stored-heredoc-regression.sh | bash'", id="quoted-file-references"),
]


@pytest.mark.parametrize("shape", STORED_RUNS)
def test_stored_heredoc_execution_refused_at_hook(env, shape):
    env["reg"].add("python -m pytest", env["fix2"])
    command = shape.format(c=f"rm -f {env['fix2']}/retention_policy.py")
    result = env["hook"](tool_name="terminal", args={"command": command, "workdir": str(env["other"])},
                         task_id=TASK)
    assert result and result["action"] == "block", (command, result)
    assert "busy-tree-guard" in result["message"]
    assert str(env["fix2"]) in result["message"]


@pytest.mark.parametrize("shape", STORED_CONTROLS)
def test_stored_heredoc_data_allowed_at_hook(env, shape):
    env["reg"].add("python -m pytest", env["fix2"])
    command = shape.format(c=f"rm -f {env['fix2']}/retention_policy.py")
    # git commit targets the idle other tree; only the INERT body names the busy tree.
    assert env["hook"](tool_name="terminal", args={"command": command, "workdir": str(env["other"])},
                       task_id=TASK) is None, command


# Variables named like shell keywords: an active expansion stays visible, quoted text stays inert.
def _kw_assigned(name):
    return "%s=$(cat <<'EOF'\n{c}\nEOF\n)\n" % name


KEYWORD_NAME_RUNS = [
    pytest.param(_kw_assigned("if") + 'eval "$if"', id="eval-dollar-if"),
    pytest.param(_kw_assigned("if") + 'eval "${{if}}"', id="eval-brace-if"),
    pytest.param(_kw_assigned("do") + 'bash -c "$do"', id="bash-c-dollar-do"),
    pytest.param(_kw_assigned("then") + 'bash -c "${{then}}"', id="bash-c-brace-then"),
]
KEYWORD_NAME_CONTROLS = [
    pytest.param(STORED_ASSIGNMENT + 'echo "if $x"', id="echo-if-word"),
    pytest.param(STORED_ASSIGNMENT + 'echo "do $x; then $x"', id="echo-do-then-words"),
    pytest.param(_kw_assigned("if") + 'echo "if $if"', id="echo-expands-if-var"),
]


@pytest.mark.parametrize("shape", KEYWORD_NAME_RUNS)
def test_keyword_named_variable_run_refused_at_hook(env, shape):
    env["reg"].add("python -m pytest", env["fix2"])
    command = shape.format(c=f"rm -f {env['fix2']}/retention_policy.py")
    result = env["hook"](tool_name="terminal", args={"command": command, "workdir": str(env["other"])},
                         task_id=TASK)
    assert result and result["action"] == "block", (command, result)
    assert "busy-tree-guard" in result["message"]


@pytest.mark.parametrize("shape", KEYWORD_NAME_CONTROLS)
def test_keyword_words_in_quotes_stay_allowed_at_hook(env, shape):
    env["reg"].add("python -m pytest", env["fix2"])
    command = shape.format(c=f"rm -f {env['fix2']}/retention_policy.py")
    assert env["hook"](tool_name="terminal", args={"command": command, "workdir": str(env["other"])},
                       task_id=TASK) is None, command

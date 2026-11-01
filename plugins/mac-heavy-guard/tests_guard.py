"""Tests for mac-heavy-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("mac_heavy_guard_plugin", "__init__.py")
guard = plugin.guard


@pytest.fixture(autouse=True)
def on_mac(monkeypatch):
    monkeypatch.setattr(guard.sys, "platform", "darwin")
    monkeypatch.delenv("MAC_HEAVY_OK", raising=False)
    monkeypatch.delenv("MAC_HEAVY_GUARD", raising=False)


def blocked(tool, **args):
    return plugin._pre_tool_call(tool_name=tool, args=args) is not None


@pytest.fixture
def copy_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / ".hermes"
    folder.mkdir()
    for name, size in (("state.db", 3 * 1024**3), ("small", 1024**2)):
        with (folder / name).open("wb") as f:
            f.truncate(size)  # sparse: no multi-GB allocation or copy
    return tmp_path


@pytest.mark.parametrize("cmd", [
    "cp ~/.hermes/state.db /tmp/x",
    "cp $HOME/.hermes/state.db /tmp/x",
    "cp ${HOME}/.hermes/state.db /tmp/x",
    "rsync -a ~/.hermes/state.db /tmp/x",
    "ditto ~/.hermes/state.db /tmp/x",
    "dd if=~/.hermes/state.db of=/tmp/x",
])
def test_large_copy_refused(copy_home, cmd):
    result = plugin._pre_tool_call(tool_name="terminal", args={"command": cmd})
    assert result and result["action"] == "block"
    for text in (str(copy_home / ".hermes/state.db"), "3.00 GiB", "3221225472",
                 "sqlite3", "mode=ro", "indexed", "MAC_HEAVY_OK=1"):
        assert text in result["message"]


@pytest.mark.parametrize("cmd", [
    "cp ~/.hermes/small /tmp/x",
    "MAC_HEAVY_OK=1 cp ~/.hermes/state.db /tmp/x",
    "cp $S/a /tmp/x",
    "cp ~/.hermes/* /tmp/x",
    "cp ~/.hermes/small ~/.hermes/state.db",
    "dd if=~/.hermes/small of=~/.hermes/state.db",
    "rsync -a ~/.hermes/small ~/.hermes/state.db",
    "ditto ~/.hermes/small ~/.hermes/state.db",
])
def test_large_copy_allow_controls(copy_home, cmd):
    assert not blocked("terminal", command=cmd)


@pytest.mark.parametrize("cmd", [
    "cp ~/.hermes/small ~/.hermes/state.db > /tmp/log",
    "cp ~/.hermes/small ~/.hermes/state.db 2>/tmp/log",
])
def test_large_copy_destination_with_redirection(copy_home, cmd):
    assert not blocked("terminal", command=cmd)


@pytest.mark.parametrize("cmd", [
    "cp .hermes/state.db /tmp/x",
    "cp -t /tmp .hermes/small .hermes/state.db",
    "rsync -e ssh .hermes/small .hermes/state.db /tmp/x",
    "ditto --arch arm64 .hermes/state.db /tmp/x",
])
def test_large_copy_cwd_and_options(copy_home, cmd):
    assert blocked("terminal", command=cmd, workdir=str(copy_home))


def test_large_copy_boundary_and_same_path_small(copy_home):
    path = copy_home / ".hermes/state.db"
    for size, expected in ((2 * 1024**3, True), (2 * 1024**3 - 1, False), (1024**2, False)):
        with path.open("r+b") as f:
            f.truncate(size)
        assert blocked("terminal", command="cp ~/.hermes/state.db /tmp/x") == expected


def test_large_copy_claude_hook(copy_home):
    result = _hook("cp ~/.hermes/state.db /tmp/x", env_extra={"HOME": str(copy_home)})
    assert result.returncode == 2 and "3.00 GiB" in result.stderr


def test_large_copy_non_mac(copy_home, monkeypatch):
    monkeypatch.setattr(guard.sys, "platform", "linux")
    assert not blocked("terminal", command="cp ~/.hermes/state.db /tmp/x")


HEAVY = [
    "cargo build --release",
    "cd ~/code/app && cargo build --release --target x86_64-apple-darwin",
    "cargo test -r",
    "cargo bench",
    "cargo test --workspace",
    "docker build -t x .",
    "docker buildx build --platform linux/amd64 .",
    "docker compose up --build",
    "pytest",
    "python -m pytest -q",
    "uv run pytest -x -q",
    "pytest -n auto tests/",
    "python3 -m pytest -n8",
    "npm test",
    "pnpm run test",
    "npx vitest run",
    "vitest",
    "npm install",
    "npm ci",
    "pnpm i",
    "yarn",
    "uv sync",
    "pip install -r requirements.txt",
    "poetry install",
    "make test",
    "nice -n 5 cargo build --release",
    "timeout 600 pytest",
    "FOO=1 cargo build --release",
    "bash -lc 'cd repo && npm ci'",
    "git pull && pnpm install && pnpm test",
    "scripts/run_tests.sh",
    "./scripts/run_tests.sh -j 8",
    "bash scripts/run_tests.sh --slice 1/2",
    "python3 scripts/run_tests_parallel.py --include-integration",
    "MAC_HEAVY_NOPE=1 scripts/run_tests.sh --file-timeout 600",
]

LIGHT = [
    "cargo build",
    "cargo check",
    "cargo test -p foo some_test",
    "pytest tests/test_x.py -q",
    "pytest tests/tools/ -k guard",
    "python -m pytest test_mac_load_recorder.py",
    "pytest -k slow_case",
    "uv run pytest tests/test_a.py::test_b",
    "npx vitest run src/foo.test.ts",
    "vitest -t 'renders'",
    "npm test -- src/foo.test.ts",
    "npm install left-pad",
    "pnpm add -D typescript",
    "pip install requests",
    "docker ps",
    "make lint",
    "ssh ubuntu@hermes-hub 'cd repo && cargo build --release'",
    "echo 'cargo build --release'",
    "git commit -m 'run pytest and npm install'",
    "rg 'docker build' docs/",
    "ls -la",
    "scripts/run_tests.sh tests/test_x.py",
    "scripts/run_tests.sh -j 4 tests/tools/",
    "scripts/run_tests.sh --files tests/a.py,tests/b.py",
    "python3 scripts/run_tests_parallel.py --files-from sel.txt",
]


def test_script_echo_naming_run_tests_is_not_the_runner(tmp_path):
    # Regression: hermes-core-wt's `echo "... with scripts/run_tests.sh"` blocked `hermes-core-wt new`.
    f = _script(tmp_path, 'echo "running ${#files[@]} test file(s) with scripts/run_tests.sh"\n'
                          './scripts/run_tests.sh "${files[@]}"\n', name="wt.sh")
    assert not blocked("terminal", command=str(f))
    g = _script(tmp_path, "cd repo && ./scripts/run_tests.sh -j 4\n", name="full.sh")
    assert blocked("terminal", command=str(g))


@pytest.mark.parametrize("cmd", HEAVY)
def test_blocks_heavy_terminal(cmd):
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", LIGHT)
def test_allows_light_terminal(cmd):
    assert not blocked("terminal", command=cmd), cmd


def test_opt_out_prefix_and_env(monkeypatch):
    assert not blocked("terminal", command="MAC_HEAVY_OK=1 cargo build --release")
    assert not blocked("terminal", command="env MAC_HEAVY_OK=1 pytest")
    assert blocked("terminal", command="MAC_HEAVY_OK=0 cargo build --release")
    monkeypatch.setenv("MAC_HEAVY_OK", "1")
    assert not blocked("terminal", command="cargo build --release")


def test_off_switch_and_non_mac(monkeypatch):
    monkeypatch.setenv("MAC_HEAVY_GUARD", "0")
    assert not blocked("terminal", command="cargo build --release")
    monkeypatch.delenv("MAC_HEAVY_GUARD")
    monkeypatch.setattr(guard.sys, "platform", "linux")
    assert not blocked("terminal", command="cargo build --release")


def test_message_names_routes_and_opt_out():
    res = plugin._pre_tool_call(tool_name="terminal", args={"command": "cargo build --release"})
    assert res["action"] == "block"
    for needle in ("cargo build --release", "worker-1", "hermes-hub", "MAC_HEAVY_OK=1", "heavy_route"):
        assert needle in res["message"]


def test_tool_call_batch_and_other_tools():
    assert blocked("tool_call", calls=[{"name": "terminal", "arguments": {"command": "uv sync"}}])
    assert not blocked("read_file", path="Cargo.toml")
    assert not blocked("terminal", command="echo 'unbalanced")


HOOK = HERE / "claude_hook.py"
# Run claude_hook.py as Claude Code does (JSON on stdin, a fresh interpreter), with sys.platform set to darwin so
# the macOS-only guard acts on any host (CI and the hub are Linux).
AS_MAC = ("import runpy, sys; sys.platform = 'darwin'; sys.argv = [sys.argv[1]]; "
          "runpy.run_path(sys.argv[0], run_name='__main__')")


def _run_hook(payload, cwd=None, env_extra=None):
    import os
    env = dict(os.environ, **(env_extra or {}))
    env.pop("MAC_HEAVY_OK", None)
    env.pop("MAC_HEAVY_GUARD", None)
    return subprocess.run([sys.executable, "-c", AS_MAC, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, cwd=cwd)


def _hook(cmd, env_extra=None):
    return _run_hook({"tool_name": "Bash", "tool_input": {"command": cmd}}, env_extra=env_extra)


def test_claude_hook_blocks_and_allows():
    r = _hook("cargo build --release")
    assert r.returncode == 2 and "MAC_HEAVY_OK=1" in r.stderr
    assert _hook("pytest tests/test_x.py").returncode == 0
    assert _hook("MAC_HEAVY_OK=1 npm ci").returncode == 0


def test_claude_hook_blocks_whole_hermes_suite():
    r = _hook("scripts/run_tests.sh -j 8")
    assert r.returncode == 2 and "run_tests.sh" in r.stderr
    assert _hook("scripts/run_tests.sh tests/test_x.py").returncode == 0
    assert _hook("MAC_HEAVY_OK=1 scripts/run_tests.sh").returncode == 0


# --- scripts: a local shell script is scanned like inline text ---------------------------------

def _script(tmp_path, body, name="ci.sh"):
    f = tmp_path / name
    f.write_text("#!/bin/bash\n" + body)
    f.chmod(0o755)
    return f


def test_heredoc_non_executable_notes_are_not_a_script(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("(ask-eve pytest; x)\n")
    notes.chmod(0o644)
    assert guard.check_shell(f"python3 - <<'EOF'\nopen('{notes}')\nEOF") is None


@pytest.mark.parametrize("form", ["absolute", "relative", "home"])
def test_direct_notes_require_execute_permission(tmp_path, monkeypatch, form):
    notes = tmp_path / "notes.md"
    notes.write_text("(ask-eve pytest; x)\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    command = {"absolute": str(notes), "relative": "./notes.md", "home": "~/notes.md"}[form]
    notes.chmod(0o644)
    assert guard.check_shell(command, cwd=str(tmp_path)) is None
    notes.chmod(0o755)
    assert guard.check_shell(command, cwd=str(tmp_path)) == (
        f"{command} → the whole pytest suite (no test path)")


@pytest.mark.parametrize("shell", ["bash", "sh", "zsh", "dash", "source", "."])
def test_explicit_shell_reads_non_executable_notes(tmp_path, shell):
    notes = tmp_path / "notes.md"
    notes.write_text("(ask-eve pytest; x)\n")
    notes.chmod(0o644)
    assert guard.check_shell(f"{shell} ./notes.md", cwd=str(tmp_path)) == (
        "./notes.md → the whole pytest suite (no test path)")


SCRIPT_BODIES = ["python3 -m pytest -q -p no:cacheprovider\n", "npm ci --prefix workers/upload\n"]


@pytest.mark.parametrize("body", SCRIPT_BODIES)
@pytest.mark.parametrize("form", ["./ci.sh", "bash ci.sh", "sh ci.sh", "(nohup ./ci.sh > log 2>&1 &)",
                                  "source ci.sh", ". ./ci.sh", "chmod +x ci.sh && ./ci.sh > ci.log 2>&1"])
def test_blocks_script_with_heavy_line(tmp_path, body, form):
    _script(tmp_path, body)
    res = plugin._pre_tool_call(tool_name="terminal", args={"command": form, "workdir": str(tmp_path)})
    assert res and res["action"] == "block", form
    assert "blocked" in res["message"] and "ci.sh \u2192" in res["message"]


def test_blocks_script_after_cd(tmp_path, monkeypatch):
    _script(tmp_path, SCRIPT_BODIES[0])
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    assert blocked("terminal", command=f"cd {tmp_path} && ./ci.sh", workdir=str(elsewhere))
    assert not blocked("terminal", command="./ci.sh", workdir=str(elsewhere))


def test_blocks_command_passed_to_function(tmp_path):
    _script(tmp_path, 'step(){ echo "=== $1"; shift; "$@"; }\nstep pytest python3 -m pytest -q\n')
    assert blocked("terminal", command="./ci.sh", workdir=str(tmp_path))
    # inline text keeps its old behaviour: arguments are not treated as commands
    assert not blocked("terminal", command="echo run python3 -m pytest -q")


def test_follows_scripts_two_levels_deep(tmp_path):
    _script(tmp_path, "python3 -m pytest -q\n", "c.sh")
    _script(tmp_path, "./c.sh\n", "b.sh")
    _script(tmp_path, "./b.sh\n", "a.sh")
    assert blocked("terminal", command="./b.sh", workdir=str(tmp_path))  # b -> c: two scripts followed
    assert not blocked("terminal", command="./a.sh", workdir=str(tmp_path))  # a -> b -> c: third not followed


def test_allows_narrow_missing_optout_and_non_shell_scripts(tmp_path):
    _script(tmp_path, "python3 -m pytest -q tests/test_one.py\n", "narrow.sh")
    assert not blocked("terminal", command="./narrow.sh", workdir=str(tmp_path))
    assert not blocked("terminal", command="./missing.sh", workdir=str(tmp_path))
    assert not blocked("terminal", command="bash missing.sh", workdir=str(tmp_path))
    _script(tmp_path, SCRIPT_BODIES[0])
    assert not blocked("terminal", command="MAC_HEAVY_OK=1 ./ci.sh", workdir=str(tmp_path))
    _script(tmp_path, "MAC_HEAVY_OK=1 python3 -m pytest -q\n", "inline_ok.sh")
    assert not blocked("terminal", command="./inline_ok.sh", workdir=str(tmp_path))
    (tmp_path / "t.py").write_text("#!/usr/bin/env python3\npytest\n")
    assert not blocked("terminal", command="./t.py", workdir=str(tmp_path))
    (tmp_path / "big.sh").write_text("#!/bin/sh\n" + "# x\n" * 20000 + "python3 -m pytest -q\n")
    assert not blocked("terminal", command="./big.sh", workdir=str(tmp_path))  # past the 64 KB read cap


def test_claude_hook_blocks_script(tmp_path):
    _script(tmp_path, SCRIPT_BODIES[0])
    r = _run_hook({"tool_name": "Bash", "tool_input": {"command": "./ci.sh > ci.log 2>&1"}}, cwd=tmp_path)
    assert r.returncode == 2 and "ci.sh" in r.stderr and "whole pytest suite" in r.stderr


def test_claude_hook_reads_the_script_from_the_session_cwd(tmp_path):
    """After a `cd` in an earlier Bash call, only the hook input's cwd names the shell's directory."""
    _script(tmp_path, SCRIPT_BODIES[0])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = _run_hook({"tool_name": "Bash", "tool_input": {"command": "./ci.sh"}, "cwd": str(tmp_path)}, cwd=elsewhere)
    assert r.returncode == 2 and "ci.sh" in r.stderr and "whole pytest suite" in r.stderr
    r = _run_hook({"tool_name": "Bash", "tool_input": {"command": "./ci.sh"}, "cwd": str(elsewhere)}, cwd=tmp_path)
    assert r.returncode == 0, "a session cwd without the script must not borrow the hook's own cwd"


def test_newline_separates_commands():
    assert blocked("terminal", command="echo start\npython3 -m pytest -q\n")
    assert blocked("terminal", command="echo start\nnpm ci\necho done\n")


def test_script_resolved_against_the_task_shell_cwd(tmp_path, monkeypatch):
    """A worker that `cd`s in one terminal call and runs ./ci.sh in the next sends no workdir."""
    _script(tmp_path, SCRIPT_BODIES[0])
    monkeypatch.setattr(plugin, "_terminal_cwd", lambda task_id: str(tmp_path) if task_id == "t1" else None)
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": "./ci.sh"}, task_id="t1")
    assert plugin._pre_tool_call(tool_name="tool_call", task_id="t1", args={
        "calls": [{"name": "terminal", "arguments": {"command": "./ci.sh"}}]})
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": "./ci.sh"}, task_id="other") is None


# --- Hermes core worktrees: multi-file test runs get a note pointing at `hermes-core-wt test` --------------------

def _repo(path, origin, run_tests=True):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    if run_tests:
        (path / "scripts").mkdir()
        _script(path / "scripts", "echo run\n", name="run_tests.sh")
    (path / "tests" / "tools").mkdir(parents=True)
    for name in ("test_a.py", "test_b.py", "tools/test_c.py"):
        (path / "tests" / name).write_text("def test_x():\n    pass\n")
    return path


@pytest.fixture
def core(tmp_path):
    return _repo(tmp_path / "core-wt", "git@github.com:daveey/hermes.git")


def _pre(command, workdir, tool_call_id="c1"):
    return plugin._pre_tool_call(tool_name="terminal", args={"command": command, "workdir": str(workdir)},
                                 task_id="t1", tool_call_id=tool_call_id)


def _post(command, workdir, result='{"output": "2 passed", "exit_code": 0}', tool_call_id="c1"):
    return plugin._transform_tool_result(tool_name="terminal", args={"command": command, "workdir": str(workdir)},
                                         result=result, task_id="t1", tool_call_id=tool_call_id)


NOTE_RUNS = [
    ("pytest tests/test_a.py tests/test_b.py -q", "hermes-core-wt test tests/test_a.py tests/test_b.py -q"),
    ("python -m pytest tests/tools", "hermes-core-wt test tests/tools"),
    ("scripts/run_tests.sh tests/test_a.py tests/test_b.py", "hermes-core-wt test tests/test_a.py tests/test_b.py"),
    ("./scripts/run_tests.sh -j 4 tests/tools/", "hermes-core-wt test -j 4 tests/tools/"),
    ("python3 scripts/run_tests_parallel.py --files tests/test_a.py,tests/test_b.py",
     "hermes-core-wt test --files tests/test_a.py,tests/test_b.py"),
    ("cd tests/.. && nice -n 5 uv run pytest tests/test_a.py tests/tools/test_c.py::test_x",
     "hermes-core-wt test tests/test_a.py tests/tools/test_c.py::test_x"),
]


@pytest.mark.parametrize("cmd,core_cmd", NOTE_RUNS)
def test_note_for_multi_file_run_in_core_worktree(core, cmd, core_cmd):
    msg = guard.note(cmd, cwd=str(core))
    assert msg and core_cmd in msg and "hermes-hub" in msg and "MacBook" in msg, msg


def test_note_for_two_file_pytest_run(core):
    assert "hermes-core-wt test tests/test_a.py tests/test_b.py" in guard.note(
        "pytest tests/test_a.py tests/test_b.py", cwd=str(core))


def test_note_for_two_file_run_tests_run(core):
    assert "hermes-core-wt test tests/test_a.py tests/test_b.py" in guard.note(
        "scripts/run_tests.sh tests/test_a.py tests/test_b.py", cwd=str(core))


@pytest.mark.parametrize("cmd", ["pytest tests/test_a.py -q", "scripts/run_tests.sh tests/test_a.py",
                                 "pytest tests/test_a.py::test_x tests/test_a.py::test_y",
                                 "pytest -k slow tests/test_a.py", "git status", "ls tests/tools"])
def test_no_note_for_single_file_or_non_test(core, cmd):
    assert guard.note(cmd, cwd=str(core)) is None
    assert _pre(cmd, core) is None and _post(cmd, core) is None


def test_no_note_outside_a_core_worktree(tmp_path):
    other = _repo(tmp_path / "other", "git@github.com:someone/else.git")
    mods = _repo(tmp_path / "mods", "git@github.com:daveey/hermes-mods.git")
    no_runner = _repo(tmp_path / "norunner", "git@github.com:daveey/hermes.git", run_tests=False)
    cmd = "pytest tests/test_a.py tests/test_b.py"
    for repo in (other, mods, no_runner):
        assert guard.note(cmd, cwd=str(repo)) is None, repo
        assert _pre(cmd, repo) is None and _post(cmd, repo) is None


def test_note_accepts_nousresearch_origin_and_subdir_cwd(tmp_path):
    up = _repo(tmp_path / "up", "https://github.com/NousResearch/hermes-agent.git")
    assert guard.note("pytest test_a.py test_b.py", cwd=str(up / "tests"))


def test_no_note_when_opted_out(core, monkeypatch):
    assert guard.note("MAC_HEAVY_OK=1 pytest tests/test_a.py tests/test_b.py", cwd=str(core)) is None
    monkeypatch.setenv("MAC_HEAVY_OK", "1")
    assert guard.note("pytest tests/test_a.py tests/test_b.py", cwd=str(core)) is None


def test_block_message_names_core_wt_test_first_in_core_worktree(core, tmp_path):
    res = _pre("scripts/run_tests.sh -j 8", core)
    assert res and res["action"] == "block"
    options = [ln for ln in res["message"].splitlines() if ln.startswith("- ")]
    assert options[0].startswith("- This is a Hermes core worktree: hermes-core-wt test -j 8 "), options
    assert "hermes-core-wt test" in _pre("python -m pytest -q", core)["message"]
    other = _repo(tmp_path / "other", "git@github.com:someone/else.git")
    assert "hermes-core-wt" not in _pre("scripts/run_tests.sh -j 8", other)["message"]
    assert "hermes-core-wt" not in _pre("cargo build --release", core)["message"]


def test_hermes_plugin_appends_note_to_the_tool_result(core):
    """Hermes entry point: pre_tool_call lets the run through; transform_tool_result appends the note."""
    cmd = "pytest tests/test_a.py tests/test_b.py"
    assert _pre(cmd, core) is None
    out = _post(cmd, core)
    assert out.startswith('{"output": "2 passed"') and "hermes-core-wt test tests/test_a.py tests/test_b.py" in out
    assert _post(cmd, core) is None, "a note is appended once"


def test_hermes_plugin_note_uses_the_cwd_before_the_command_ran(core, monkeypatch):
    """`cd <wt> && pytest ...` with no workdir: the note is computed from the task shell's cwd at call time."""
    monkeypatch.setattr(plugin, "_terminal_cwd", lambda task_id: str(core.parent))
    cmd = "cd core-wt && pytest tests/test_a.py tests/test_b.py"
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": cmd}, task_id="t1", tool_call_id="c9") is None
    monkeypatch.setattr(plugin, "_terminal_cwd", lambda task_id: str(core))  # the shell has moved by now
    out = plugin._transform_tool_result(tool_name="terminal", args={"command": cmd}, result="ok", task_id="t1",
                                        tool_call_id="c9")
    assert out and "hermes-core-wt test" in out


def test_hermes_plugin_registers_both_hooks():
    hooks = []

    class Ctx:
        def register_hook(self, name, fn):
            hooks.append(name)

    plugin.register(Ctx())
    assert hooks == ["pre_tool_call", "transform_tool_result"]


def test_claude_hook_emits_note_as_additional_context(core):
    """Claude Code entry point: exit 0 (not blocked) with hookSpecificOutput.additionalContext."""
    r = _run_hook({"tool_name": "Bash", "cwd": str(core),
                   "tool_input": {"command": "pytest tests/test_a.py tests/test_b.py"}})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and "permissionDecision" not in out
    assert "hermes-core-wt test tests/test_a.py tests/test_b.py" in out["additionalContext"]
    single = _run_hook({"tool_name": "Bash", "cwd": str(core), "tool_input": {"command": "pytest tests/test_a.py"}})
    assert single.returncode == 0 and single.stdout == ""


def test_claude_hook_block_names_core_wt_test_in_core_worktree(core):
    r = _run_hook({"tool_name": "Bash", "cwd": str(core), "tool_input": {"command": "scripts/run_tests.sh"}})
    assert r.returncode == 2 and "hermes-core-wt test runs it on hermes-hub" in r.stderr

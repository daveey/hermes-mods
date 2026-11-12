"""Tests for mac-heavy-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import json
import os
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
                 "sqlite3", "mode=ro", "indexed", "MAC_HEAVY_OK=1",
                 "PRAGMA query_only=ON", "state.db?mode=rw"):  # the fallback when mode=ro can't open a WAL db
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


def test_script_array_literal_naming_run_tests_is_data(tmp_path):
    # Regression (11-02): hermes-core-wt's `local here=(./scripts/run_tests.sh)` array literal read as a
    # whole-suite run, so `hermes-core-wt new/test <paths>` (the command this guard recommends) was blocked.
    f = _script(tmp_path, 'local here=(./scripts/run_tests.sh)\n'
                          'here=(env X="${X:-4}" nice -n 10 ./scripts/run_tests.sh)\n'
                          'files+=(./scripts/run_tests.sh)\n'
                          '"${here[@]}" "$@"\n', name="wt.sh")
    assert not blocked("terminal", command=str(f))
    # A real run after the array, or a command substitution, is still caught.
    g = _script(tmp_path, "here=(a b)\n./scripts/run_tests.sh -j 4\n", name="after.sh")
    assert blocked("terminal", command=str(g))
    assert blocked("terminal", command="x=$(./scripts/run_tests.sh -j 4)")


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


def test_message_routes_to_a_worker_card_first_not_raw_ssh_on_the_hub():
    """agent-24dac018a6: a Mac session followed "Anywhere: run it on the hub" and ran review tests and a CI wait
    over raw ssh on hermes-hub while the hub refused Kanban claims at ~85% CPU against its 50% gate. Raw ssh skips
    that admission gate; a pool-worker card obeys it. The first route is the worker card; no raw hub-ssh recipe."""
    res = plugin._pre_tool_call(tool_name="terminal", args={"command": "cargo build --release"})
    options = [ln for ln in res["message"].splitlines() if ln.startswith("- ")]
    assert "pool-worker card" in options[0] and "runs_on" in options[0], options
    assert "ssh ubuntu@hermes-hub" not in res["message"]
    assert "Anywhere: run it on the hub" not in res["message"]
    assert "hub-runtime" in res["message"]


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


@pytest.mark.parametrize("cmd", [
    "echo hi # note\nuv sync",
    "echo hi # note\r\nuv sync",
    "echo hi; # note\nuv sync",
    "(echo hi) # note\nuv sync",
    "echo 'it''s' # a 'quote\nuv sync",  # a quote inside a comment opens nothing
    "# note\nuv sync",
    "echo a#b\nuv sync",
])
def test_comment_keeps_its_newline(cmd):
    assert guard.check_shell(cmd) is not None, cmd


@pytest.mark.parametrize("cmd", [
    "echo hi # uv sync",
    "echo hi #uv sync",
    "# uv sync\necho done",
    "echo hi # note\necho uv sync",
])
def test_comment_text_is_not_a_command(cmd):
    assert guard.check_shell(cmd) is None, cmd


@pytest.mark.parametrize("text, want", [
    ("echo a#b", ["echo", "a#b"]),
    ("echo '#' \"#x\" \\#y", ["echo", "#", "#x", "#y"]),
    ("echo hi # note\nls", ["echo", "hi", "\n", "ls"]),
])
def test_hash_inside_a_word_or_quotes_is_not_a_comment(text, want):
    assert guard._tokens(text) == want


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


# --- heredocs fed to ssh: the body runs on the remote host -----------------------------------------------------

# The command the terminal tool refused (host address replaced by a hostname): the `uv sync` runs on the hub.
SSH_HEREDOC = (
    "timeout 120 ssh -o BatchMode=yes -o ConnectTimeout=20 ubuntu@hermes-hub 'bash -s' <<'EOF' 2>&1"
    " | grep -v '^hub SSH route:'\n"
    "export TZ=America/Los_Angeles\n"
    "env -u VIRTUAL_ENV $HOME/.local/bin/uv sync --project $HOME/.hermes/hermes-agent --locked --inexact"
    " --group dev --group test --check > /tmp/x 2>&1\n"
    "EOF\n"
)


def test_ssh_heredoc_body_is_remote():
    assert guard.check_shell(SSH_HEREDOC) is None


@pytest.mark.parametrize("cmd", [
    "ssh hub bash -s <<EOF\nuv sync\nEOF",
    "ssh hub bash -s <<-EOF\n\tuv sync\n\tEOF",
    "ssh hub bash -s <<-'EOF'\n\tcargo build --release\n\t\tEOF\n",
])
def test_ssh_heredoc_unquoted_and_tab_stripped_delimiters(cmd):
    assert guard.check_shell(cmd) is None, cmd


def test_ssh_heredoc_after_a_redirect():
    assert guard.check_shell("ssh hub bash -s 2>&1 <<'EOF'\nuv sync\nEOF") is None


@pytest.mark.parametrize("cmd", [
    "ssh hub \\\n  bash -s <<'EOF'\nuv sync\nEOF",  # continued line: ssh still owns the operator
    "cat <<A; ssh hub bash -s <<'B'\nhello\nA\nuv sync\nB",  # two heredocs on one line, bodies in order
    "ssh hub bash -s <<'EOF'\nuv sync\n",  # no delimiter line: the body runs to the end
])
def test_ssh_heredoc_continued_several_and_unterminated(cmd):
    assert guard.check_shell(cmd) is None, cmd


@pytest.mark.parametrize("cmd", [
    "bash -s <<'EOF'\nuv sync\nEOF",  # a local shell's heredoc runs here
    "ssh hub bash -s <<'EOF'\necho hi\nEOF\nuv sync",  # after the delimiter line: local again
    'ssh hub "echo <<EOF"\nuv sync',  # an operator inside quotes is no operator
    "ssh hub bash -s <<< x\nuv sync",  # a here-string has no body
    "ssh hub bash -s <<'B'; cat <<A\nx\nB\nuv sync\nA",  # second body is local
    "ssh hub bash -s <<-EOF\n\techo hi\n\tEOF\nuv sync",  # a tab-indented <<- delimiter ends the body
])
def test_heredoc_bodies_that_run_here_are_still_scanned(cmd):
    assert guard.check_shell(cmd), cmd


def test_no_note_for_a_multi_file_run_inside_an_ssh_heredoc(core):
    # note() drops ssh heredoc bodies too: a multi-file run sent to another host puts no load on this Mac.
    cmd = "ssh hub bash -s <<'EOF'\npytest tests/test_a.py tests/test_b.py\nEOF"
    assert guard.note(cmd, cwd=str(core)) is None
    assert _pre(cmd, core) is None and _post(cmd, core) is None


@pytest.mark.parametrize("cmd", [
    "ssh hub true # <<EOF\nuv sync\nEOF",  # an operator in a comment is no operator
    "ssh hub 'x\n<<EOF'\nuv sync\nEOF",  # nor one inside a quote opened on an earlier line
    "bash -s <<'EOF'\nssh hub bash -s <<X\nuv sync\nX\nEOF",  # a local body is left exactly as it was
])
def test_pre_pass_keeps_text_without_an_ssh_heredoc(cmd):
    assert guard._drop_ssh_heredocs(cmd) == cmd


def test_hermes_plugin_allows_ssh_heredoc():
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": SSH_HEREDOC}) is None


def test_claude_hook_allows_ssh_heredoc():
    r = _hook(SSH_HEREDOC)
    assert r.returncode == 0 and r.stderr == "", r.stderr


# A redirect is not a test path: `pytest 2>&1 | tail` read `2>` as a path and passed as a targeted run.
@pytest.mark.parametrize("cmd", ["pytest 2>&1 | tail", "pytest 2>/dev/null", "( pytest 2>/dev/null )",
                                 "pytest -q &>/tmp/log", "pytest >/tmp/log 2>&1", "npm install 2>&1",
                                 "cd /tmp && pytest 2>&1 | tail -5"])
def test_redirect_is_not_a_test_path(cmd):
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", ["pytest tests/test_x.py 2>&1 | tail", "pytest -q tests/test_x.py 2>/dev/null",
                                 "( pytest tests/test_x.py 2>&1 )", "npm install left-pad 2>&1",
                                 "pytest tests/test_x.py < /dev/null"])
def test_redirect_targeted_runs_allowed(cmd):
    assert not blocked("terminal", command=cmd), cmd


# A quoted `<`/`>` is a test argument, not a redirect: `pytest '<' tests/test_x.py` dropped `<` and the path after it
# and read a whole-suite run. An input dup (`2<&1`) is a redirect with no path.
@pytest.mark.parametrize("cmd", ["pytest '<' tests/test_x.py", "pytest \">>\" tests/test_x.py 2>&1",
                                 "pytest tests/test_x.py 2<&1", "pytest tests/test_x.py <&- | tail"])
def test_quoted_redirect_and_input_dup_targeted(cmd):
    assert not blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", ["pytest 2<&1", "pytest <&- | tail", "pytest -q 0<&- 2>&1 | grep -c '>'"])
def test_input_dup_whole_suite_still_blocked(cmd):
    assert blocked("terminal", command=cmd), cmd


# Bash runs a command substitution inside double quotes (and in backticks) like an unquoted one, but shlex kept
# `"$(...)"` as one data word: `out="$(docker build -t x . 2>&1)"` passed while `out=$(docker build ...)` was refused.
@pytest.mark.parametrize("cmd", ['out="$(docker build -t x . 2>&1)"', "out=$(docker build -t x . 2>&1)",
                                 'echo "`docker build -t x .`"', "echo `docker build -t x .`",
                                 'echo "done: $(uv sync 2>&1 | tail -1)"', 'r="$(pytest -q 2>&1)"',
                                 'echo "$(echo "$(cargo build --release)")"'])
def test_double_quoted_substitution_runs_like_unquoted(cmd):
    assert guard.check_shell(cmd, os.path.expanduser("~")), cmd
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", ["echo '$(docker build -t x .)'", 'echo "\\$(docker build -t x .)"',
                                 'echo "\\`docker build -t x .\\`"', 'out="$(git status 2>&1)"',
                                 'out="$(ls tests 2>/dev/null)"', 'r="$(pytest -q tests/test_x.py 2>&1)"',
                                 'echo hi # "$(docker build -t x .)"'])
def test_inert_substitution_shapes_stay_allowed(cmd):
    assert guard.check_shell(cmd, os.path.expanduser("~")) is None, cmd
    assert not blocked("terminal", command=cmd), cmd


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
def test_heredoc_and_comment_apostrophes_hide_no_command(shape):
    cmd = shape.format(c="docker build -t x .")
    assert guard.check_shell(cmd, os.path.expanduser("~")), cmd
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("shape", HEREDOC_ALLOWED + [
    "ssh host <<'EOF'\nit's remote\ndocker build -t x .\nEOF", "python3 - <<'EOF'\nprint(\"it's\")\nEOF"])
def test_heredoc_and_comment_data_stays_allowed(shape):
    cmd = shape.format(c="docker build -t x .", safe="ls plugins")
    assert guard.check_shell(cmd, os.path.expanduser("~")) is None, cmd
    assert not blocked("terminal", command=cmd), cmd


def test_command_after_ssh_heredoc_with_apostrophe_is_checked():
    cmd = "ssh host <<'EOF'\nit's remote\nEOF\ndocker build -t x ."
    assert guard.check_shell(cmd, os.path.expanduser("~")), cmd


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
    "git commit -m \"$(cat <<'EOF'\nwe don't {c}\nEOF\n)\"",
    "git commit -m \"$(date)\" -m \"$(cat <<'EOF'\nwe don't {c}\nEOF\n)\"",
    "gh pr create --title t --body \"$(cat <<'EOF'\nit's {c}\nEOF\n)\"",
    "x=$(cat <<'EOF'\nit's {c}\nEOF\n)",
    "export MSG=\"$(cat <<'EOF'\nit's {c}\nEOF\n)\"",
    "cat > /tmp/s.sh <<'EOF'\nit's {c}\nEOF\nbash /tmp/other.sh",
]


@pytest.mark.parametrize("shape", READER_SHAPES)
def test_heredoc_body_a_shell_may_read_is_checked(shape):
    cmd = shape.format(c="docker build -t x .")
    assert guard.check_shell(cmd, os.path.expanduser("~")), cmd
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("shape", READER_ALLOWED)
def test_heredoc_body_a_data_reader_reads_stays_allowed(shape):
    cmd = shape.format(c="docker build -t x .")
    assert guard.check_shell(cmd, os.path.expanduser("~")) is None, cmd
    assert not blocked("terminal", command=cmd), cmd


# Store data first, then run it in the same tool call. Test actual admission, not extraction.
STORED_ASSIGNMENT = "x=$(cat <<'EOF'\n{c}\nEOF\n)\n"
STORED_FILE = "cat > /tmp/stored-heredoc-regression.sh <<'EOF'\n{c}\nEOF\n"
STORED_RUNS = [
    pytest.param(STORED_ASSIGNMENT + 'eval "$x"', id="assignment-eval"),
    pytest.param(STORED_ASSIGNMENT + 'bash -c "$x"', id="assignment-bash-c"),
    pytest.param(STORED_ASSIGNMENT + '$x', id="assignment-command"),
    pytest.param(STORED_ASSIGNMENT + 'source <(printf "%s\\n" "$x")', id="assignment-source"),
    pytest.param(STORED_FILE + 'bash < /tmp/stored-heredoc-regression.sh', id="file-shell-stdin"),
    pytest.param(STORED_FILE + 'cat /tmp/stored-heredoc-regression.sh | bash', id="file-pipe-shell"),
    pytest.param(STORED_FILE + 'sh -c "$(cat /tmp/stored-heredoc-regression.sh)"', id="file-shell-cat"),
    pytest.param(STORED_FILE + 'eval "$(< /tmp/stored-heredoc-regression.sh)"', id="file-eval-read"),
]
STORED_CONTROLS = [
    pytest.param(STORED_ASSIGNMENT, id="assignment-no-run"),
    pytest.param('git commit -m "$(cat <<\'EOF\'\n{c}\nEOF\n)"', id="commit-message"),
    pytest.param(STORED_FILE + 'cat /tmp/stored-heredoc-regression.sh', id="write-then-cat"),
    pytest.param(STORED_ASSIGNMENT + "echo 'eval \"$x\"; bash -c \"$x\"; $x'", id="quoted-variable-references"),
    pytest.param(STORED_FILE + "echo 'bash < /tmp/stored-heredoc-regression.sh; cat /tmp/stored-heredoc-regression.sh | bash'", id="quoted-file-references"),
]


@pytest.mark.parametrize("shape", STORED_RUNS)
def test_stored_heredoc_execution_refused_at_hook(shape):
    command = shape.format(c="docker build -t x .")
    result = plugin._pre_tool_call(tool_name="terminal", args={"command": command})
    assert result and result["action"] == "block", (command, result)
    assert "mac-heavy-guard" in result["message"]


@pytest.mark.parametrize("shape", STORED_CONTROLS)
def test_stored_heredoc_data_allowed_at_hook(shape):
    command = shape.format(c="docker build -t x .")
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": command}) is None, command


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
def test_keyword_named_variable_run_refused_at_hook(shape):
    command = shape.format(c="docker build -t x .")
    result = plugin._pre_tool_call(tool_name="terminal", args={"command": command})
    assert result and result["action"] == "block", (command, result)
    assert "mac-heavy-guard" in result["message"]


@pytest.mark.parametrize("shape", KEYWORD_NAME_CONTROLS)
def test_keyword_words_in_quotes_stay_allowed_at_hook(shape):
    command = shape.format(c="docker build -t x .")
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": command}) is None, command

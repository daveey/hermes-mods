"""live-checkout-guard: the live CORE checkout (~/.hermes/hermes-agent) is protected too.

Run: python3 -m pytest -q -p no:cacheprovider tests_core_root.py

Why: on 2026-11-12 a Kanban worker agent ran the command in EXACT_1432Z in the live core checkout of a
worker node (no coach plugin there). Nothing refused it; HEAD went detached and the core updater skipped
every pass for 1h25m ("checkout not on main").
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("live_checkout_guard_plugin_core", "__init__.py")


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=ENV)


def _repo(path: Path, origin: Path):
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, capture_output=True)
    (path / "hermes_cli").mkdir()
    (path / "hermes_cli" / "main.py").write_text("x = 1\n")
    (path / ".gitignore").write_text("logs/\nvenv/\nnode_modules/\n")
    git("add", ".", cwd=path)
    git("commit", "-qm", "init", cwd=path)
    git("push", "-q", "origin", "HEAD:main", cwd=path)
    git("fetch", "-q", "origin", cwd=path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """HOME with a live core checkout at ~/.hermes/hermes-agent (the default location) and a mods root elsewhere."""
    home = tmp_path / "home"
    core = home / ".hermes" / "hermes-agent"
    core.parent.mkdir(parents=True)
    _repo(core, tmp_path / "core-origin.git")
    mods = tmp_path / "mods"
    mods.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LIVE_CHECKOUT_ROOT", str(mods))
    for k in ("LIVE_CORE_CHECKOUT", "LIVE_CHECKOUT_GUARD", "TERMINAL_CWD"):
        monkeypatch.delenv(k, raising=False)
    return type("E", (), dict(core=core, mods=mods, other=other, home=home, tmp=tmp_path))


def term(cmd, cwd=None):
    return plugin._pre_tool_call("terminal", {"command": cmd, **({"workdir": str(cwd)} if cwd else {})})


def blocked(r):
    return bool(r) and r.get("action") == "block"


# The exact 14:32:10Z command (path elided by the agent as `…`; a concrete file stands in for it).
EXACT_1432Z = ("cd ~/.hermes/hermes-agent && git checkout -q --detach 2>/dev/null; "
               "git show origin/main:hermes_cli/main.py | sed -n '1,40p'")


def test_exact_1432z_command_refused(env):
    r = term(EXACT_1432Z, cwd=env.other)
    assert blocked(r)
    msg = r["message"]
    assert "git checkout" in msg
    assert "core checkout" in msg
    assert "hermes-core-wt new" in msg
    assert "git -C ~/.hermes/hermes-agent show origin/main:<path>" in msg


@pytest.mark.parametrize("cmd", [
    "git -C ~/.hermes/hermes-agent checkout --detach",
    "git -C ~/.hermes/hermes-agent switch --detach",
    "git -C ~/.hermes/hermes-agent checkout main",
    "git -C ~/.hermes/hermes-agent reset --hard origin/main",
    "git -C ~/.hermes/hermes-agent rebase origin/main",
    "git -C ~/.hermes/hermes-agent commit -am x",
    "git -C ~/.hermes/hermes-agent stash",
    "git -C ~/.hermes/hermes-agent cherry-pick abc123",
    "git -C ~/.hermes/hermes-agent pull --ff-only",
    "bash -lc 'cd ~/.hermes/hermes-agent && git switch -c x'",
    "GIT_DIR=~/.hermes/hermes-agent/.git git checkout --detach",
])
def test_core_head_moves_refused(env, cmd):
    assert blocked(term(cmd, cwd=env.other)), cmd


def test_core_ff_only_merge_refused_even_clean(env):
    # The mods root allows a clean ff-only merge; the core checkout is the updater's alone (HEAD move =
    # every running backend reports code skew).
    r = term("git -C ~/.hermes/hermes-agent merge --ff-only origin/main", cwd=env.other)
    assert blocked(r)
    assert "core checkout" in r["message"]


@pytest.mark.parametrize("cmd", [
    "git -C ~/.hermes/hermes-agent show origin/main:hermes_cli/main.py",
    "git -C ~/.hermes/hermes-agent log -3 --oneline",
    "git -C ~/.hermes/hermes-agent status -sb",
    "git -C ~/.hermes/hermes-agent fetch origin",
    "git -C ~/.hermes/hermes-agent diff HEAD origin/main",
    "git -C ~/.hermes/hermes-agent stash list",
    "cd ~/.hermes/hermes-agent && git show origin/main:hermes_cli/main.py | sed -n 1,5p",
    "cd ~/.hermes/hermes-agent && grep -rn foo hermes_cli",
])
def test_core_reads_allowed(env, cmd):
    assert not term(cmd, cwd=env.other), cmd


def test_core_worktree_add_outside_allowed(env):
    cmd = f"git -C ~/.hermes/hermes-agent worktree add --detach {env.other}/wt origin/main"
    assert not term(cmd, cwd=env.other)


def test_core_tracked_writes_refused(env):
    assert blocked(plugin._pre_tool_call("write_file", {"path": str(env.core / "hermes_cli" / "main.py"),
                                                        "content": "x"}))
    assert blocked(plugin._pre_tool_call("patch", {"mode": "replace", "path": "~/.hermes/hermes-agent/hermes_cli/main.py",
                                                   "old_string": "x", "new_string": "y"}))
    assert blocked(term("sed -i s/1/2/ hermes_cli/main.py", cwd=env.core))
    assert blocked(term("echo y > ~/.hermes/hermes-agent/hermes_cli/new.py", cwd=env.other))


def test_core_gitignored_writes_allowed_except_node_modules(env):
    # Logs, venv and build output are gitignored runtime state (coach's rule): writes there stay allowed.
    assert not term("echo y >> ~/.hermes/hermes-agent/logs/x.log", cwd=env.other)
    assert not plugin._pre_tool_call("write_file", {"path": str(env.core / "logs" / "x.log"), "content": "x"})
    assert not term("rm -rf ~/.hermes/hermes-agent/venv/tmpdir", cwd=env.other)
    # node_modules is gitignored but is the live dependency install.
    assert blocked(term("cp -r /tmp/pkg ~/.hermes/hermes-agent/node_modules/pkg", cwd=env.other))


def test_core_execute_code_literal_write_refused(env):
    code = f"open({str(env.core / 'hermes_cli' / 'main.py')!r}, 'w').write('x')\n"
    assert blocked(plugin._pre_tool_call("execute_code", {"code": code}))


def test_core_override_and_disable(env, monkeypatch):
    elsewhere = env.tmp / "core2"
    _repo(elsewhere, env.tmp / "core2-origin.git")
    monkeypatch.setenv("LIVE_CORE_CHECKOUT", str(elsewhere))
    assert blocked(term(f"git -C {elsewhere} checkout --detach", cwd=env.other))
    for off in ("0", "off", ""):
        monkeypatch.setenv("LIVE_CORE_CHECKOUT", off)
        assert not term(f"git -C {elsewhere} checkout --detach", cwd=env.other), off
        assert not term(EXACT_1432Z, cwd=env.other), off


def test_no_core_checkout_no_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LIVE_CHECKOUT_ROOT", str(tmp_path / "mods"))
    monkeypatch.delenv("LIVE_CORE_CHECKOUT", raising=False)
    plain = tmp_path / ".hermes" / "hermes-agent"
    plain.mkdir(parents=True)  # not a git checkout: nothing to protect
    assert not term(f"git -C {plain} checkout --detach", cwd=tmp_path)


def test_running_core_from_loaded_hermes_cli(env, monkeypatch):
    # The mini runs core from ~/code/hermes-agent (~/.hermes is a symlink elsewhere): the in-process
    # hermes_cli module locates the live checkout, like coach's default_live_checkout().
    runtime = env.tmp / "code-hermes-agent"
    _repo(runtime, env.tmp / "rt-origin.git")
    fake = type(sys)("hermes_cli")
    fake.__file__ = str(runtime / "hermes_cli" / "__init__.py")
    monkeypatch.setitem(sys.modules, "hermes_cli", fake)
    assert blocked(term(f"cd {runtime} && git checkout -q --detach", cwd=env.other))
    assert blocked(term(EXACT_1432Z, cwd=env.other))  # ~/.hermes/hermes-agent stays protected as well


def test_claude_hook_refuses_core_head_move(env):
    import json
    p = subprocess.run([sys.executable, str(HERE / "claude_hook.py")], input=json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": EXACT_1432Z}, "cwd": str(env.other)}),
        capture_output=True, text=True, env={**os.environ, "HOME": str(env.home),
                                              "LIVE_CHECKOUT_ROOT": str(env.mods)})
    assert p.returncode == 2 and "core checkout" in p.stderr


def test_core_escape_hatch_is_per_command_and_core_only(env):
    assert not term("COACH_ALLOW_LIVE=1 git -C ~/.hermes/hermes-agent checkout main", cwd=env.other)
    # scoped to that one command: the next segment is still checked
    assert blocked(term("COACH_ALLOW_LIVE=1 git -C ~/.hermes/hermes-agent status; "
                        "git -C ~/.hermes/hermes-agent checkout --detach", cwd=env.other))
    subprocess.run(["git", "init", "-q", str(env.mods)], check=True)
    assert blocked(term("COACH_ALLOW_LIVE=1 git checkout --detach", cwd=env.mods))


def test_mods_root_semantics_unchanged(env):
    git_mods = env.mods
    subprocess.run(["git", "init", "-q", str(git_mods)], check=True)
    assert blocked(term("git checkout --detach", cwd=git_mods))
    r = plugin._pre_tool_call("write_file", {"path": str(git_mods / "x.py"), "content": "x"})
    assert blocked(r) and "core checkout" not in r["message"]

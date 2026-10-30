"""Tests for live-checkout-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import json
import os
import subprocess
import sys
import types
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


plugin = _load("live_checkout_guard_plugin", "__init__.py")
guard = plugin.guard


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=ENV)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A 'live' checkout (with origin), a detached worktree outside it, a symlink into it."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    live = tmp_path / "live"
    subprocess.run(["git", "clone", "-q", str(origin), str(live)], check=True, capture_output=True)
    (live / "a.txt").write_text("a\n")
    git("add", ".", cwd=live)
    git("commit", "-qm", "init", cwd=live)
    git("push", "-q", "origin", "HEAD:main", cwd=live)
    git("fetch", "-q", "origin", cwd=live)
    wt = tmp_path / "wt"
    git("worktree", "add", "-q", "--detach", str(wt), "origin/main", cwd=live)
    link = tmp_path / "link"
    link.symlink_to(live)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("LIVE_CHECKOUT_ROOT", str(live))
    monkeypatch.delenv("LIVE_CHECKOUT_GUARD", raising=False)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    return type("E", (), dict(live=live, wt=wt, link=link, other=other, tmp=tmp_path))


def term(cmd, cwd=None):
    return plugin._pre_tool_call("terminal", {"command": cmd, **({"workdir": str(cwd)} if cwd else {})})


def blocked(r):
    return bool(r) and r.get("action") == "block"


# ---- patch / write_file ----------------------------------------------------------------------

def test_write_file_in_live_blocked(env):
    r = plugin._pre_tool_call("write_file", {"path": str(env.live / "x.py"), "content": "x"})
    assert blocked(r)
    assert "git" in r["message"] and "worktree add" in r["message"] and "--detach" in r["message"]


def test_write_file_via_symlink_blocked(env):
    assert blocked(plugin._pre_tool_call("write_file", {"path": str(env.link / "x.py"), "content": "x"}))


def test_write_file_via_symlinked_file_blocked(env):
    (env.other / "f").symlink_to(env.live / "a.txt")
    assert blocked(plugin._pre_tool_call("write_file", {"path": str(env.other / "f"), "content": "x"}))


def test_write_file_in_worktree_and_elsewhere_allowed(env):
    assert not plugin._pre_tool_call("write_file", {"path": str(env.wt / "x.py"), "content": "x"})
    assert not plugin._pre_tool_call("write_file", {"path": str(env.other / "x.py"), "content": "x"})


def test_patch_replace_and_v4a_blocked(env):
    assert blocked(plugin._pre_tool_call("patch", {"mode": "replace", "path": str(env.live / "a.txt"),
                                                   "old_string": "a", "new_string": "b"}))
    v4a = f"*** Begin Patch\n*** Update File: {env.live}/a.txt\n-a\n+b\n*** End Patch"
    assert blocked(plugin._pre_tool_call("patch", {"mode": "patch", "patch": v4a}))
    ok = f"*** Begin Patch\n*** Update File: {env.wt}/a.txt\n-a\n+b\n*** End Patch"
    assert not plugin._pre_tool_call("patch", {"mode": "patch", "patch": ok})


def test_mcp_prefixed_tool_names(env):
    assert blocked(plugin._pre_tool_call("mcp__write_file", {"path": str(env.live / "x"), "content": ""}))


def test_tilde_default_root(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVE_CHECKOUT_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "code" / "hermes-mods").mkdir(parents=True)
    assert blocked(plugin._pre_tool_call("write_file", {"path": "~/code/hermes-mods/x", "content": ""}))


def test_skill_manage_allowed(env):
    assert not plugin._pre_tool_call("skill_manage", {"operations": [
        {"action": "patch", "name": "x", "old_string": "a", "new_string": "b"}]})


def test_disabled(env, monkeypatch):
    monkeypatch.setenv("LIVE_CHECKOUT_GUARD", "0")
    assert not plugin._pre_tool_call("write_file", {"path": str(env.live / "x"), "content": ""})


# ---- git ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("sub", ["commit -m x", "stash", "stash pop", "rebase origin/main", "reset --hard",
                                 "checkout main", "checkout -- a.txt", "restore a.txt", "apply p.diff",
                                 "am p.mbox", "cherry-pick abc", "merge feature", "switch -c x"])
def test_git_mutations_blocked_by_cwd(env, sub):
    r = term(f"git {sub}", env.live)
    assert blocked(r), sub
    assert "worktree add" in r["message"]


def test_git_dash_C_blocked_and_allowed(env):
    assert blocked(term(f"git -C {env.live} commit -m x", env.other))
    assert blocked(term(f"git -C {env.link} commit -m x", env.other))
    assert not term(f"git -C {env.wt} commit -m x", env.live)
    assert not term("git commit -m x", env.wt)


def test_git_after_cd_and_compound(env):
    assert blocked(term(f"cd {env.live} && git commit -m x", env.other))
    assert blocked(term(f"cd {env.live}; echo hi; git reset --hard", env.other))
    assert blocked(term("ls | wc -l && git stash", env.live))
    assert blocked(term("cd .. && cd live && git checkout x", env.other))
    assert not term(f"cd {env.wt} && git commit -m x", env.live)
    assert blocked(term(f"bash -c 'cd {env.live} && git commit -m x'", env.other))


def test_git_read_fetch_worktree_allowed(env):
    for c in ["git status", "git log --oneline", "git diff", "git fetch origin", "git show HEAD",
              "git branch -a", "git stash list", "git add -A",
              f"git worktree add --detach {env.tmp}/n origin/main", "git rev-parse HEAD", "ls -la", "cat a.txt"]:
        assert not term(c, env.live), c


def test_ff_only_clean_allowed(env):
    assert not term("git merge --ff-only origin/main", env.live)
    assert not term(f"git -C {env.live} merge --ff-only origin/main", env.other)


def test_ff_only_dirty_refused(env):
    (env.live / "a.txt").write_text("dirty\n")
    r = term("git merge --ff-only origin/main", env.live)
    assert blocked(r) and "uncommitted" in r["message"]
    (env.live / "a.txt").write_text("a\n")
    (env.live / "new.txt").write_text("untracked\n")
    assert blocked(term("git merge --ff-only origin/main", env.live))


def test_merge_without_ff_only_or_other_ref_refused(env):
    assert blocked(term("git merge origin/main", env.live))
    assert blocked(term("git merge --ff-only feature", env.live))


def test_hermes_sync_allowed(env):
    assert not term("~/code/hermes/hermes-sync run", env.live)
    assert not term("hermes-sync run", env.live)


# ---- shell writes ------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["sed -i 's/a/b/' a.txt", "sed -i.bak s/a/b/ a.txt", "sed --in-place s/a/b/ a.txt",
                                 "sed -ni 's/a/b/p' a.txt", "echo hi > out.txt", "echo hi >> out.txt",
                                 "echo hi >out.txt", "echo hi 2> err.txt", "echo hi &> all.txt",
                                 "echo hi | tee out.txt", "echo hi | tee -a out.txt", "perl -pi -e 's/a/b/' a.txt",
                                 "rm a.txt", "mv a.txt b.txt", "touch n.txt", "cp /etc/hosts a.txt",
                                 "cat <<EOF > n.txt\nhi\nEOF"])
def test_shell_writes_by_cwd_blocked(env, cmd):
    assert blocked(term(cmd, env.live)), cmd


def test_shell_writes_absolute_and_symlink_targets(env):
    assert blocked(term(f"echo hi > {env.live}/x", env.other))
    assert blocked(term(f"echo hi > {env.link}/x", env.other))
    assert blocked(term(f"echo hi | tee {env.link}/x", env.other))
    assert blocked(term(f"sed -i s/a/b/ {env.live}/a.txt", env.other))
    assert blocked(term(f"cd {env.live} && echo hi > x", env.other))
    (env.other / "f").symlink_to(env.live / "a.txt")
    assert blocked(term("echo hi > f", env.other))
    assert blocked(term("sed -i s/a/b/ f", env.other))


def test_shell_writes_outside_allowed(env):
    for c in [f"echo hi > {env.wt}/x", "echo hi > out.txt", "sed -i s/a/b/ a.txt", "echo hi | tee x",
              "echo hi 2>&1", "echo hi > /dev/null", f"cp {env.live}/a.txt {env.other}/a.txt",
              f"sed s/a/b/ {env.live}/a.txt", f"cat {env.live}/a.txt"]:
        assert not term(c, env.other), c
    assert not term("sed -i s/a/b/ a.txt", env.wt)
    assert not term("grep a a.txt 2>&1 >/dev/null", env.live)
    assert not term("echo hi 2>/dev/null", env.live)


# ---- execute_code ------------------------------------------------------------------------------

def code(src):
    return plugin._pre_tool_call("execute_code", {"code": src})


def test_execute_code_embedded_calls(env):
    live = env.live
    assert blocked(code(f"from hermes_tools import terminal\nterminal('git -C {live} commit -m x')"))
    assert blocked(code(f"terminal(command='echo hi > {live}/x')"))
    assert blocked(code(f"terminal('ls', workdir='{live}')\nterminal('git reset --hard', workdir='{live}')"))
    assert blocked(code(f"write_file('{live}/x.py', 'x')"))
    assert blocked(code(f"patch(path='{live}/a.txt', old_string='a', new_string='b')"))
    assert blocked(code(f"import subprocess\nsubprocess.run(['git','-C','{live}','commit','-m','x'])"))
    assert blocked(code(f"import subprocess\nsubprocess.run('git stash', shell=True, cwd='{live}')"))
    assert blocked(code(f"import os\nos.system('echo hi > {live}/x')"))
    assert blocked(code(f"open('{live}/x', 'w').write('x')"))
    assert blocked(code(f"from pathlib import Path\nPath('{live}/x').write_text('x')"))
    assert blocked(code(f"open('{env.link}/x', 'a').write('x')"))


def test_execute_code_allowed(env):
    assert not code(f"terminal('git -C {env.wt} commit -m x')")
    assert not code(f"terminal('git status', workdir='{env.live}')")
    assert not code(f"open('{env.live}/a.txt').read()")
    assert not code(f"write_file('{env.wt}/x', 'x')")
    assert not code("x = (")  # syntax error: never break a call


def test_execute_code_dynamic_not_inspected(env):
    """Documented limitation: non-literal arguments are invisible to the hook."""
    assert not code(f"p = '{env.live}' + '/x'\nopen(p, 'w').write('x')")


def test_tool_call_batch_recursion(env):
    r = plugin._pre_tool_call("mcp__tool_call", {"calls": [
        {"name": "write_file", "arguments": {"path": str(env.live / "x"), "content": ""}}]})
    assert blocked(r)


# ---- Claude adapter ----------------------------------------------------------------------------

def run_claude(payload, env_):
    return subprocess.run([sys.executable, str(HERE / "claude_hook.py")], input=json.dumps(payload),
                          capture_output=True, text=True, env={**os.environ, "LIVE_CHECKOUT_ROOT": str(env_.live)})


def test_claude_bash_blocks_and_allows(env):
    p = run_claude({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": str(env.live)}, env)
    assert p.returncode == 2 and "worktree add" in p.stderr
    p = run_claude({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": str(env.wt)}, env)
    assert p.returncode == 0
    p = run_claude({"tool_name": "Bash", "tool_input": {"command": "git merge --ff-only origin/main"},
                    "cwd": str(env.live)}, env)
    assert p.returncode == 0


def test_claude_edit_tools_blocked(env):
    p = run_claude({"tool_name": "Write", "tool_input": {"file_path": str(env.live / "x")}, "cwd": str(env.other)}, env)
    assert p.returncode == 2
    p = run_claude({"tool_name": "Edit", "tool_input": {"file_path": str(env.wt / "x")}, "cwd": str(env.other)}, env)
    assert p.returncode == 0


def test_claude_bad_payload_fails_open(env):
    p = subprocess.run([sys.executable, str(HERE / "claude_hook.py")], input="not json", capture_output=True, text=True)
    assert p.returncode == 0


def test_claude_install_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "echo other"}]}]}, "model": "x"}))
    for _ in range(2):
        p = subprocess.run([sys.executable, str(HERE / "claude_hook.py"), "--install", "--settings", str(settings)],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
    data = json.loads(settings.read_text())
    cmds = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    ours = [e["matcher"] for e in data["hooks"]["PreToolUse"]
            for h in e["hooks"] if "live-checkout-guard" in h["command"] and "claude_hook.py" in h["command"]]
    assert sorted(ours) == ["Bash", "Write|Edit|MultiEdit|NotebookEdit"]  # once each after two installs
    assert "echo other" in cmds and data["model"] == "x"
    assert any(e["matcher"] == "Write|Edit|MultiEdit|NotebookEdit" for e in data["hooks"]["PreToolUse"])


# ---- review fixes: session cwd, wrappers, shell -c clusters, --git-dir, pull, positional patch, ln ---------

def test_session_cwd_from_core_record_wins(env, monkeypatch):
    """No workdir: the terminal session's recorded cwd (core get_session_cwd(task_id)) decides, not TERMINAL_CWD."""
    rec = {"T-live": str(env.live), "T-wt": str(env.wt)}
    fake = types.SimpleNamespace(get_session_cwd=lambda k: rec.get(k))
    monkeypatch.setitem(sys.modules, "tools.terminal_tool", fake)
    monkeypatch.setenv("TERMINAL_CWD", str(env.other))
    hooks = {}
    plugin.register(types.SimpleNamespace(register_hook=lambda name, cb: hooks.setdefault(name, cb)))
    cb = hooks["pre_tool_call"]
    assert blocked(cb(tool_name="terminal", args={"command": "git commit -m x"}, task_id="T-live"))
    assert blocked(cb(tool_name="write_file", args={"path": "x.py", "content": "x"}, task_id="T-live"))
    assert blocked(cb(tool_name="execute_code", args={"code": "terminal('git stash')"}, task_id="T-live"))
    assert not cb(tool_name="terminal", args={"command": "git commit -m x"}, task_id="T-wt")
    # explicit workdir still beats the record
    assert not cb(tool_name="terminal", args={"command": "git commit -m x", "workdir": str(env.wt)}, task_id="T-live")
    # unknown session: falls back to TERMINAL_CWD (other) -> allowed
    assert not cb(tool_name="terminal", args={"command": "git commit -m x"}, task_id="T-none")


def _core_python():
    cand = [os.environ.get("HERMES_CORE_PYTHON", "")]
    cand += sorted(str(p) for p in Path.home().glob(".hermes/installs/*/environments/*/venv/bin/python"))
    core = Path(os.environ.get("HERMES_AGENT_DIR", str(Path.home() / ".hermes/hermes-agent")))
    for py in filter(None, cand):
        ok = subprocess.run([py, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import tools.terminal_tool",
                             str(core)], capture_output=True, timeout=120)
        if ok.returncode == 0:
            return py, core
    return None, core


def test_real_core_registered_hook_uses_persisted_terminal_cwd(env, tmp_path):
    """End to end in the real Hermes core: register() via PluginContext, `cd` through the real terminal tool, then
    the real pre_tool_call dispatch with that task_id; plus the execute_code RPC dispatch path."""
    py, core = _core_python()
    if not py:
        pytest.skip("no Hermes runtime python that can import tools.terminal_tool (set HERMES_CORE_PYTHON)")
    home = tmp_path / "hermes-home"
    home.mkdir()
    e = {**ENV, "HERMES_HOME": str(home), "LIVE_CHECKOUT_ROOT": str(env.live), "TERMINAL_CWD": str(env.other)}
    p = subprocess.run([py, str(HERE / "_core_probe.py"), str(core), str(env.live), str(env.wt), str(env.other)],
                       capture_output=True, text=True, env=e, timeout=240)
    assert p.returncode == 0, p.stderr[-3000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert os.path.realpath(out["A_recorded"]) == os.path.realpath(env.live)
    assert out["A_commit_blocked"] and out["A_write_rel_blocked"] and out["A_status_allowed"], out
    assert os.path.realpath(out["B_recorded"]) == os.path.realpath(env.wt)
    assert out["B_commit_allowed"] and out["C_rpc_blocked"], out


@pytest.mark.parametrize("cmd", ["nice -n 5 git commit -m x", "nice -n5 git commit -m x", "nice -5 git commit -m x",
                                 "sudo -u root git commit -m x", "sudo -u root -- git commit -m x",
                                 "sudo --user=root git commit -m x", "sudo --user root git commit -m x",
                                 "env -u FOO git commit -m x", "nice -n 5 sudo -u root env A=1 git reset --hard",
                                 "timeout 5 git commit -m x", "timeout -s KILL 5 git stash",
                                 "nice -n 5 rm a.txt", "sudo -u root sed -i s/a/b/ a.txt"])
def test_wrapper_value_flags_blocked(env, cmd):
    assert blocked(term(cmd, env.live)), cmd


def test_wrapper_chdir_and_worktree_allowed(env):
    assert blocked(term(f"env -C {env.live} git commit -m x", env.other))
    assert blocked(term(f"env --chdir={env.live} git commit -m x", env.other))
    assert blocked(term(f"sudo -D {env.live} git commit -m x", env.other))
    assert blocked(term("env -S 'git commit -m x'", env.live))
    assert not term("env -S 'git commit -m x'", env.wt)
    assert not term(f"env -C {env.live} git status; git commit -m x", env.other)  # -C scoped to one command
    assert not term("nice -n 5 git commit -m x", env.wt)
    assert not term("sudo -u root git commit -m x", env.wt)
    assert not term(f"env -C {env.wt} git commit -m x", env.live)


@pytest.mark.parametrize("cmd", ["bash -lc 'git commit -m x'", "bash -l -c 'git commit -m x'",
                                 "zsh -ic 'git stash'", "sh -ec 'rm a.txt'", "bash -o pipefail -c 'git reset --hard'",
                                 "bash --norc -c 'git commit -m x'", "nice -n 5 bash -lc 'git commit -m x'"])
def test_shell_c_clusters_blocked(env, cmd):
    assert blocked(term(cmd, env.live)), cmd


def test_shell_c_clusters_allowed(env):
    assert not term("bash -lc 'git commit -m x'", env.wt)
    assert not term("bash -lc 'git status'", env.live)
    assert not term(f"bash -lc 'cd {env.wt} && git commit -m x'", env.live)


def test_git_dir_targeting_live_blocked(env):
    g = env.live / ".git"
    assert blocked(term(f"git --git-dir={g} commit -m x", env.other))
    assert blocked(term(f"git --git-dir {g} --work-tree {env.other} reset --hard", env.other))
    assert blocked(term(f"git --git-dir={env.link}/.git stash", env.other))
    assert blocked(term(f"GIT_DIR={g} git commit -m x", env.other))
    assert blocked(term(f"GIT_WORK_TREE={env.live} git checkout -- a.txt", env.other))


def test_git_dir_reads_and_other_repos_allowed(env):
    assert not term(f"git --git-dir={env.live}/.git log --oneline", env.other)
    assert not term(f"git --git-dir={env.wt}/.git commit -m x", env.other)
    assert not term(f"GIT_DIR={env.live}/.git git status", env.other)


def test_git_pull_blocked_even_ff_only_clean(env):
    for c in ["git pull", "git pull --ff-only", "git pull --ff-only origin main", "git pull --rebase"]:
        assert blocked(term(c, env.live)), c
    assert blocked(term(f"git -C {env.live} pull --ff-only", env.other))
    assert not term("git pull --ff-only", env.wt)
    assert not term("git merge --ff-only origin/main", env.live)  # the one sanctioned update stays allowed


def test_execute_code_positional_patch(env):
    assert blocked(code(f"patch('{env.live}/a.txt', 'a', 'b')"))
    assert blocked(code(f"from hermes_tools import patch\npatch('{env.link}/a.txt', 'a', 'b', True)"))
    assert not code(f"patch('{env.wt}/a.txt', 'a', 'b')")


def test_ln_source_in_live_allowed(env):
    assert not term(f"ln -s {env.live}/a.txt {env.other}/lnk", env.other)
    assert not term(f"ln -s {env.live}/a.txt lnk", env.other)
    assert not term(f"ln -sf {env.live}/a.txt lnk", env.other)
    assert not term(f"ln -s {env.live}/a.txt", env.other)        # one operand: link made in cwd (other)
    assert not term(f"ln -s {env.live} {env.tmp}/live-link", env.other)
    assert not term(f"ln -s -t {env.other} {env.live}/a.txt", env.live)


def test_ln_destination_in_live_blocked(env):
    assert blocked(term(f"ln -s /etc/hosts {env.live}/x", env.other))
    assert blocked(term("ln -s /etc/hosts x", env.live))
    assert blocked(term(f"ln -s {env.other}/f", env.live))       # one operand: link made in cwd (live)
    assert blocked(term(f"ln -s -t {env.live} /etc/hosts", env.other))
    assert blocked(term(f"ln -s --target-directory={env.live} /etc/hosts", env.other))
    assert blocked(term(f"ln -s /etc/hosts {env.link}/y", env.other))

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

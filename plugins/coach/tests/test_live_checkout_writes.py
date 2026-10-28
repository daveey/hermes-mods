"""pre_tool_call refuses file writes into the live Hermes checkout (they land `.dirty` in `hermes update` versions
and can be imported live by running backends). Reads and gitignored paths stay allowed; COACH_ALLOW_LIVE=1 passes."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_rules as rules  # noqa: E402

blk = rules.blocks_live_checkout


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_ALLOW_LIVE", raising=False)
    home = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(home))
    root = home / ".hermes" / "hermes-agent"
    (root / "hermes_cli").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "hermes_cli" / "kanban_wake_check.py").write_text("x = 1\n")
    (root / "run_agent.py").write_text("y = 1\n")
    (root / ".gitignore").write_text("logs/\nvenv/\n*.log\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    (home / "scratch").mkdir()
    return root


# The 2026-10-28 incident shape: cd into the live tree, heredoc python rewrites tracked files.
INCIDENT = ("cd ~/.hermes/hermes-agent && python3 - <<'EOF'\n"
            "p = 'hermes_cli/kanban_wake_check.py'\n"
            "s = open(p).read()\n"
            "open(p, 'w').write(s.replace('x = 1', 'x = 2'))\n"
            "EOF")


def test_heredoc_python_write_into_live_tree_is_refused(live):
    what = blk("terminal", {"command": INCIDENT}, live)
    assert what and "hermes_cli/kanban_wake_check.py" in what
    assert blk("terminal", {"command": "python3 - <<EOF\nfrom pathlib import Path\n"
                            f"Path('{live}/run_agent.py').write_text('z')\nEOF"}, live)
    assert blk("terminal", {"command": "python3 - <<'EOF'\nimport pathlib\n"
                            "pathlib.Path('run_agent.py').write_text('z')\nEOF", "workdir": str(live)}, live)
    assert blk("terminal", {"command": f"cd {live} && python3 -c \"open('run_agent.py','w').write('z')\""}, live)


def test_shell_writes_into_live_tree_are_refused(live):
    for cmd in (f"cd {live} && sed -i 's/x/y/' hermes_cli/kanban_wake_check.py",
                f"sed -i.bak 's/x/y/' {live}/run_agent.py",
                f"cd {live} && perl -pi -e 's/x/y/' run_agent.py",
                f"echo x > {live}/run_agent.py",
                f"cd {live} && cat > hermes_cli/new_mod.py <<'EOF'\nprint(1)\nEOF",
                f"cp /tmp/a.py {live}/hermes_cli/kanban_wake_check.py",
                f"echo x | tee ~/.hermes/hermes-agent/run_agent.py",
                f"cd {live} && rm run_agent.py",
                f"cd {live} && git stash",
                f"git -C {live} apply /tmp/fix.patch",
                f"cd {live} && patch -p1 < /tmp/fix.patch"):
        assert blk("terminal", {"command": cmd}, live), cmd


def test_patch_and_write_file_on_live_tree_paths_are_refused(live):
    assert blk("write_file", {"path": str(live / "run_agent.py"), "content": "z"}, live)
    assert blk("write_file", {"path": "~/.hermes/hermes-agent/hermes_cli/new.py", "content": "z"}, live)
    assert blk("patch", {"path": str(live / "hermes_cli" / "kanban_wake_check.py"),
                         "old_string": "x", "new_string": "y"}, live)
    v4a = f"*** Begin Patch\n*** Update File: {live}/run_agent.py\n@@\n-y = 1\n+y = 2\n*** End Patch"
    assert blk("patch", {"mode": "patch", "patch": v4a}, live)


def test_execute_code_writes_into_live_tree_are_refused(live):
    assert blk("execute_code", {"code": f"open('{live}/run_agent.py','w').write('z')"}, live)
    assert blk("execute_code", {"code": "from hermes_tools import terminal\n"
                                f"terminal('cd {live} && sed -i s/x/y/ run_agent.py')"}, live)
    assert not blk("execute_code", {"code": f"print(open('{live}/run_agent.py').read())"}, live)
    assert not blk("execute_code", {"code": f"open('/tmp/out.txt','w').write('x')"}, live)


def test_read_only_and_out_of_tree_commands_pass(live, tmp_path):
    scratch = tmp_path / "scratch"
    for cmd in (f"cd {live} && sed -n 1,40p hermes_cli/kanban_wake_check.py",
                f"cd {live} && grep -rn wake hermes_cli | head",
                f"cat {live}/run_agent.py",
                f"cd {live} && git log --oneline -3 && git status --short && git diff",
                f"cd {live} && git diff > {scratch}/x.diff",
                f"cd {live} && git stash list",
                f"git -C {live} apply --check /tmp/fix.patch",
                f"cd {live} && git checkout -- run_agent.py",
                f"cp {live}/run_agent.py {scratch}/copy.py",
                f"cd {live} && python3 - <<'EOF'\nimport sys\nsys.stdout.write(open('run_agent.py').read())\nEOF",
                f"cd {live} && python3 - <<'EOF'\nprint(open('hermes_cli/kanban_wake_check.py').read())\nEOF",
                f"cd {live} && python3 - <<'EOF'\nopen('/tmp/out.txt','w').write('x')\nEOF",
                f"cd {scratch} && sed -i s/a/b/ notes.txt; grep x {live}/run_agent.py",
                f"cd {live} && echo hi >> logs/agent.log",
                f"echo x > {live}/venv/marker",
                f"cd {live} && cat <<'EOF'\nopen('run_agent.py','w').write('x')\nEOF"):
        assert not blk("terminal", {"command": cmd}, live), cmd
    assert not blk("write_file", {"path": str(live / "logs" / "a.log"), "content": "x"}, live)
    assert not blk("write_file", {"path": str(scratch / "a.py"), "content": "x"}, live)
    assert not blk("read_file", {"path": str(live / "run_agent.py")}, live)


def test_coach_allow_live_passes(live, monkeypatch):
    assert not blk("terminal", {"command": "COACH_ALLOW_LIVE=1 " + INCIDENT}, live)
    assert not blk("terminal", {"command": f"COACH_ALLOW_LIVE=1 sed -i s/x/y/ {live}/run_agent.py"}, live)
    assert blk("write_file", {"path": str(live / "run_agent.py")}, live)
    monkeypatch.setenv("COACH_ALLOW_LIVE", "1")
    assert not blk("write_file", {"path": str(live / "run_agent.py")}, live)
    assert not blk("patch", {"path": str(live / "run_agent.py")}, live)


def test_pre_tool_call_hook_refuses_live_writes(live, tmp_path):
    spec = importlib.util.spec_from_file_location("coach_pkg_init_w", HERE.parent / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    _, hooks = mod.build(store=core.Store(root=tmp_path / "coach"), checkout=live)
    out = hooks["pre_tool_call"](tool_name="terminal", args={"command": INCIDENT})
    assert out and out["action"] == "block"
    assert "hermes-core-wt new" in out["message"] and ".dirty" in out["message"]
    out = hooks["pre_tool_call"](tool_name="patch", args={"path": str(live / "run_agent.py")})
    assert out and out["action"] == "block"
    assert hooks["pre_tool_call"](tool_name="terminal", args={"command": f"cd {live} && git status"}) is None

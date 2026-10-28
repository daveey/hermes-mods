"""Tests for cpu-load-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cpu_load_guard_plugin", HERE / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


@pytest.fixture(autouse=True)
def protected(monkeypatch):
    monkeypatch.setenv("CPU_LOAD_GUARD_HOSTS", plugin.guard.socket.gethostname())
    monkeypatch.setenv("CPU_LOAD_GUARD_NCPU", "16")
    monkeypatch.delenv("CPU_LOAD_GUARD", raising=False)


def run(tool, args):
    return plugin._pre_tool_call(tool_name=tool, args=args)


BAD = [
    "for i in $(seq 1 $(nproc)); do yes >/dev/null & done",
    "for i in $(seq 1 16); do yes > /dev/null & done",
    "for i in {1..16}; do yes >/dev/null & done; sleep 60",
    "n=$(sysctl -n hw.ncpu); for i in $(seq $n); do yes >/dev/null & done",
    "stress-ng --cpu $(nproc) --timeout 60s",
    "stress-ng --cpu 0",
    "stress --cpu 16 --timeout 30",
    "openssl speed -multi 16 sha256",
    "openssl speed -multi $(nproc)",
    "seq 16 | xargs -P 16 -I{} yes >/dev/null",
    "for i in $(seq 1 12); do while :; do :; done & done",
    "for ((i=0;i<16;i++)); do cat /dev/zero >/dev/null & done",
    "yes>/dev/null & " * 8,
]
GOOD = [
    "pytest -n 2",
    "pytest -n $(nproc) tests/",
    "make -j$(nproc)",
    "yes | head -5",
    "yes >/dev/null &",
    "for i in 1 2; do yes >/dev/null & done",
    "stress-ng --cpu 2 --timeout 5s",
    "openssl speed -multi 2 sha256",
    "echo yes; grep -c yes file",
    "seq 1 100 | xargs -P 2 echo",
    "for i in $(seq 1 $(nproc)); do echo $i; done",
]


@pytest.mark.parametrize("cmd", BAD)
def test_terminal_blocks(cmd):
    r = run("terminal", {"command": cmd})
    assert r and r["action"] == "block" and "hermes-hub" in r["message"]


@pytest.mark.parametrize("cmd", BAD)
def test_background_terminal_blocks(cmd):
    r = run("terminal", {"command": cmd, "background": True, "notify": True})
    assert r and r["action"] == "block"


@pytest.mark.parametrize("cmd", GOOD)
def test_terminal_allows(cmd):
    assert run("terminal", {"command": cmd}) is None
    assert run("terminal", {"command": cmd, "background": True}) is None


EXEC_BAD = [
    "import subprocess\nsubprocess.Popen('stress-ng --cpu 16', shell=True)",
    "from hermes_tools import terminal\nterminal('for i in $(seq 1 $(nproc)); do yes >/dev/null & done', background=True)",
    "import subprocess, os\nprocs=[subprocess.Popen(['yes'], stdout=subprocess.DEVNULL) for _ in range(os.cpu_count())]",
    "import multiprocessing as mp\ndef burn():\n    while True:\n        pass\nps=[mp.Process(target=burn) for _ in range(mp.cpu_count())]",
    "import subprocess\nfor _ in range(16):\n    subprocess.Popen(['yes'], stdout=subprocess.DEVNULL)",
]


@pytest.mark.parametrize("code", EXEC_BAD)
def test_execute_code_blocks(code):
    r = run("execute_code", {"code": code})
    assert r and r["action"] == "block"


def test_execute_code_allows():
    assert run("execute_code", {"code": "import subprocess\nsubprocess.run(['pytest','-n','2'])"}) is None
    assert run("execute_code", {"code": "import os\nprint(os.cpu_count())"}) is None
    assert run("execute_code", {"code": "x=[i*i for i in range(1000)]"}) is None


def test_execute_code_quoted_yes_without_spawn_allows():
    # A quoted 'yes' that is never launched is not a burner, even beside a wide range().
    assert run("execute_code", {"code": "ans = 'yes'\nfor i in range(100): print(i)"}) is None
    assert run("execute_code", {"code": "if reply in ('yes', 'y'):\n    for i in range(64): pass"}) is None
    assert run("execute_code", {"code": "names = ['stress', 'yes']\nprint(len(range(32)))"}) is None


# --- the incident vector: write a load script, then run it -------------------------------------

# Reconstruction of the worker's load-suite.sh (run 2): a loader starting one `yes` per core,
# pgrep checks, then ten suite runs.
LOAD_SUITE = """#!/bin/bash
set -u
NCPU=$(sysctl -n hw.ncpu)
start_load() {
  for i in $(seq 1 "$NCPU"); do
    yes > /dev/null &
  done
}
start_load
for run in $(seq 1 10); do
  yes=$(pgrep -x yes | wc -l)
  echo "RUN $run at $(date +%T) yes=$yes load=$(uptime)"
  python -m pytest -q tests/test_browser_agent_retirement.py
done
"""
LOADER_NOHUP = "#!/bin/sh\nnohup bash -c 'for i in $(seq $(sysctl -n hw.ncpu)); do yes >/dev/null & done; wait' &\n"
PY_LOADER = "import os, subprocess\nfor _ in range(os.cpu_count()):\n    subprocess.Popen(['yes'], stdout=subprocess.DEVNULL)\n"


@pytest.mark.parametrize("content", [LOAD_SUITE, LOADER_NOHUP])
@pytest.mark.parametrize("name", ["load-suite.sh", "load-suite", "run.bash"])
def test_write_file_of_load_script_blocks(tmp_path, content, name):
    r = run("write_file", {"path": str(tmp_path / name), "content": content})
    assert r and r["action"] == "block" and "hermes-hub" in r["message"]


def test_write_file_of_python_loader_blocks(tmp_path):
    r = run("write_file", {"path": str(tmp_path / "load.py"), "content": PY_LOADER})
    assert r and r["action"] == "block"


def test_patch_adding_load_blocks(tmp_path):
    p = tmp_path / "load-suite.sh"
    r = run("patch", {"path": str(p), "old_string": "start_load\n", "new_string": LOAD_SUITE})
    assert r and r["action"] == "block"
    r = run("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Update File: {tmp_path}/x.sh\n+" + "\n+".join(LOAD_SUITE.splitlines())})
    assert r and r["action"] == "block"
    r = run("mcp__skill_manage", {"operations": []})
    assert r is None


def test_write_file_allows_normal_scripts_and_docs(tmp_path):
    assert run("write_file", {"path": str(tmp_path / "ok.sh"), "content": "#!/bin/bash\npytest -n 2\n"}) is None
    assert run("write_file", {"path": str(tmp_path / "ok.py"), "content": "ans = 'yes'\nfor i in range(100): print(i)\n"}) is None
    # Documentation that quotes the pattern is not executable.
    assert run("write_file", {"path": str(tmp_path / "NOTES.md"), "content": LOAD_SUITE}) is None


@pytest.mark.parametrize("fmt", [
    "bash {p}", "sh {p}", "zsh {p}", "source {p}", ". {p}", "bash -x {p} arg", "cd /tmp && bash {p}",
    "nohup bash {p} > log 2>&1 &", "{p}", "./{n}",
])
def test_terminal_running_load_script_blocks(tmp_path, fmt):
    p = tmp_path / "load-suite.sh"
    p.write_text(LOAD_SUITE)
    p.chmod(0o755)
    cmd = fmt.format(p=p, n=p.name)
    r = run("terminal", {"command": cmd, "workdir": str(tmp_path)})
    assert r and r["action"] == "block", cmd
    r = run("terminal", {"command": cmd, "workdir": str(tmp_path), "background": True})
    assert r and r["action"] == "block", cmd


def test_terminal_running_python_loader_blocks(tmp_path):
    p = tmp_path / "load.py"
    p.write_text(PY_LOADER)
    for cmd in (f"python3 {p}", f"python {p}", "python3 load.py", "uv run python load.py"):
        r = run("terminal", {"command": cmd, "workdir": str(tmp_path)})
        assert r and r["action"] == "block", cmd


def test_terminal_script_nested_one_level_blocks(tmp_path):
    (tmp_path / "loader.sh").write_text(LOADER_NOHUP)
    (tmp_path / "suite.sh").write_text(f"#!/bin/bash\nbash {tmp_path}/loader.sh\npytest -q\n")
    r = run("terminal", {"command": f"bash {tmp_path}/suite.sh"})
    assert r and r["action"] == "block"


def test_execute_code_running_load_script_blocks(tmp_path):
    p = tmp_path / "load-suite.sh"
    p.write_text(LOAD_SUITE)
    code = f"from hermes_tools import terminal\nterminal('bash {p}')"
    r = run("execute_code", {"code": code})
    assert r and r["action"] == "block"


def test_terminal_running_benign_script_allows(tmp_path):
    p = tmp_path / "ok.sh"
    p.write_text("#!/bin/bash\npytest -n 2\nfor i in $(seq 1 $(nproc)); do echo $i; done\n")
    assert run("terminal", {"command": f"bash {p}"}) is None
    assert run("terminal", {"command": "bash /nonexistent/x.sh"}) is None
    assert run("terminal", {"command": "source ~/.bashrc && echo hi"}) is None


def test_terminal_inline_python_blocks():
    cmd = "python3 -c 'import subprocess,os;[subprocess.Popen([\"yes\"],stdout=-3) for _ in range(os.cpu_count())]'"
    r = run("terminal", {"command": cmd})
    assert r and r["action"] == "block"
    heredoc = "python3 - <<'EOF'\n" + PY_LOADER + "EOF"
    r = run("terminal", {"command": heredoc})
    assert r and r["action"] == "block"
    assert run("terminal", {"command": "python3 -c 'print(\"yes\"); print(list(range(100)))'"}) is None


def test_wrapped_tool_call_and_process_write():
    r = run("tool_call", {"calls": [{"name": "mcp__terminal", "arguments": {"command": "stress-ng --cpu 16"}}]})
    assert r and r["action"] == "block"
    assert run("process_manage", {"action": "submit", "data": "stress-ng --cpu 16"})


def test_unprotected_host_allows(monkeypatch):
    monkeypatch.setenv("CPU_LOAD_GUARD_HOSTS", "some-other-host")
    assert run("terminal", {"command": "stress-ng --cpu $(nproc)"}) is None


def test_off_switch(monkeypatch):
    monkeypatch.setenv("CPU_LOAD_GUARD", "off")
    assert run("terminal", {"command": "stress-ng --cpu $(nproc)"}) is None


def test_default_protects_laptop_and_hub(monkeypatch):
    monkeypatch.delenv("CPU_LOAD_GUARD_HOSTS")
    monkeypatch.setenv("HERMES_HOME", "/nonexistent")
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "David-MBP.local")
    assert plugin.guard.enabled()
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "hermes-hub")
    assert plugin.guard.enabled()
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "unrelated-host")
    assert not plugin.guard.enabled()


# --- relative script paths: `cd` in the command and the terminal's persisted cwd ----------------

def _load_dir(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    p = d / "load-suite.sh"
    p.write_text(LOAD_SUITE)
    p.chmod(0o755)
    return d


@pytest.mark.parametrize("fmt", [
    "cd {d} && ./load-suite.sh",
    "cd {d}; bash load-suite.sh",
    "cd {d} && nohup ./load-suite.sh > log 2>&1 &",
    "pushd {d} >/dev/null && sh load-suite.sh",
    "cd {parent} && cd repo && bash load-suite.sh",
    "cd ~/{rel} && ./load-suite.sh",
])
def test_cd_then_relative_script_blocks(tmp_path, monkeypatch, fmt):
    d = _load_dir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir("/")  # the agent process is elsewhere
    cmd = fmt.format(d=d, parent=tmp_path, rel="repo")
    for bg in (False, True):
        r = run("terminal", {"command": cmd, "background": bg})
        assert r and r["action"] == "block", cmd


def test_cd_elsewhere_then_relative_script_allows(tmp_path, monkeypatch):
    d = _load_dir(tmp_path)
    monkeypatch.chdir(d)  # the script is in the process cwd, but the command cds away first
    other = tmp_path / "other"
    other.mkdir()
    (other / "load-suite.sh").write_text("#!/bin/bash\npytest -n 2\n")
    assert run("terminal", {"command": f"cd {other} && bash load-suite.sh"}) is None


def test_execute_code_cd_then_relative_script_blocks(tmp_path, monkeypatch):
    d = _load_dir(tmp_path)
    monkeypatch.chdir("/")
    code = f"from hermes_tools import terminal\nterminal('cd {d} && ./load-suite.sh')"
    r = run("execute_code", {"code": code})
    assert r and r["action"] == "block"


def _fake_terminal_tool(monkeypatch, session_cwd=None, env_cwd=None):
    import sys
    import threading
    import types

    fake = types.ModuleType("tools.terminal_tool")
    fake._env_lock = threading.Lock()
    env = types.SimpleNamespace(cwd=env_cwd) if env_cwd else None
    fake._active_environments = {"default": env} if env else {}
    fake._resolve_container_task_id = lambda tid: "default"
    fake._lookup_active_env = lambda eff, tid: fake._active_environments.get(eff)
    fake.get_session_cwd = lambda key: session_cwd
    pkg = types.ModuleType("tools")
    pkg.terminal_tool = fake
    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.terminal_tool", fake)


@pytest.mark.parametrize("cmd", ["bash load-suite.sh", "./load-suite.sh", "sh load-suite.sh &"])
@pytest.mark.parametrize("where", ["env", "session"])
def test_persisted_terminal_cwd_script_blocks(tmp_path, monkeypatch, cmd, where):
    d = _load_dir(tmp_path)
    monkeypatch.chdir("/")
    _fake_terminal_tool(monkeypatch, **({"env_cwd": str(d)} if where == "env" else {"session_cwd": str(d)}))
    r = plugin._pre_tool_call(tool_name="terminal", args={"command": cmd}, task_id="t1", session_id="s1")
    assert r and r["action"] == "block", (cmd, where)
    code = f"from hermes_tools import terminal\nterminal('{cmd}')"
    r = plugin._pre_tool_call(tool_name="execute_code", args={"code": code}, task_id="t1")
    assert r and r["action"] == "block", (cmd, where)


def test_persisted_cwd_lookup_failure_is_harmless(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "tools.terminal_tool", None)  # import fails
    monkeypatch.chdir(tmp_path)
    assert run("terminal", {"command": "bash load-suite.sh"}) is None
    _load_dir(tmp_path)
    assert run("terminal", {"command": "cd repo && bash load-suite.sh"})["action"] == "block"


def _spaced_dir(tmp_path):
    d = tmp_path / "with space"
    d.mkdir()
    p = d / "load-suite.sh"
    p.write_text(LOAD_SUITE)
    p.chmod(0o755)
    return d


@pytest.mark.parametrize("fmt", [
    "cd '{d}' && ./load-suite.sh",
    'cd "{d}"; bash load-suite.sh',
    "pushd '{d}' >/dev/null && sh load-suite.sh",
    'cd "{s}" && ./load-suite.sh',
    "cd '{s}'; bash load-suite.sh",
    'bash "{s}/load-suite.sh"',
    "source '{s}/load-suite.sh'",
    '"{s}/load-suite.sh" &',
    "nohup '{s}/load-suite.sh' >/dev/null 2>&1 &",
])
def test_quoted_cd_and_script_paths_block(tmp_path, monkeypatch, fmt):
    d = _load_dir(tmp_path)
    s = _spaced_dir(tmp_path)
    monkeypatch.chdir("/")
    cmd = fmt.format(d=d, s=s)
    for bg in (False, True):
        r = run("terminal", {"command": cmd, "background": bg})
        assert r and r["action"] == "block", cmd
    code = "from hermes_tools import terminal\nterminal(" + repr(cmd) + ")"
    r = run("execute_code", {"code": code})
    assert r and r["action"] == "block", cmd


def test_quoted_python_file_blocks(tmp_path, monkeypatch):
    s = tmp_path / "with space"
    s.mkdir()
    (s / "burn.py").write_text(
        "import os, subprocess\nfor _ in range(os.cpu_count()):\n    subprocess.Popen(['yes'], stdout=subprocess.DEVNULL)\n"
    )
    monkeypatch.chdir("/")
    for cmd in (f'python3 "{s}/burn.py"', f"cd '{s}' && python3 burn.py"):
        r = run("terminal", {"command": cmd})
        assert r and r["action"] == "block", cmd


def test_quoted_cd_elsewhere_allows(tmp_path, monkeypatch):
    d = _load_dir(tmp_path)
    monkeypatch.chdir(d)
    other = tmp_path / "other dir"
    other.mkdir()
    (other / "load-suite.sh").write_text("#!/bin/bash\npytest -n 2\n")
    assert run("terminal", {"command": f'cd "{other}" && bash load-suite.sh'}) is None
    assert run("terminal", {"command": f"echo 'cd {d}' && bash --version"}) is None


def test_nested_script_relative_path_uses_caller_cwd(tmp_path, monkeypatch):
    # bash resolves a relative path inside outer.sh against the caller's cwd, not outer.sh's directory.
    d = _load_dir(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "outer.sh").write_text("#!/bin/bash\nbash load-suite.sh\n")
    monkeypatch.chdir("/")
    r = run("terminal", {"command": f"cd {d} && bash {scripts}/outer.sh"})
    assert r and r["action"] == "block"


# --- a file named to a reader is data, not a script the command runs -----------------------
# Incident: `grep -nE "follow-through" ~/code/hermes-mods/README.md` was refused because the
# closing quote was treated as a command separator and README.md (which documents stress-ng) was
# scanned as if it had been executed.

BURN_SH = "#!/bin/bash\nstress-ng --cpu 16\n"
README_DOCS = "hub load: stress-ng --cpu 16; also `stress-ng --cpu`/`openssl speed -multi`\n"


def test_executing_burn_sh_still_blocks(tmp_path):
    p = tmp_path / "burn.sh"
    p.write_text(BURN_SH)
    p.chmod(0o755)
    for cmd in (f"bash {p}", "bash burn.sh", f"sh {p}", f"./{p.name}"):
        r = run("terminal", {"command": cmd, "workdir": str(tmp_path)})
        assert r and r["action"] == "block", cmd
    assert run("terminal", {"command": "stress-ng --cpu 16"})["action"] == "block"
    assert run("terminal", {"command": "for i in $(seq 1 16); do yes >/dev/null & done"})["action"] == "block"


@pytest.mark.parametrize("fmt", [
    "grep stress {p}",
    'grep "stress" {p}',
    "grep stress '{p}'",
    'grep stress "{p}"',
    "grep stress burn.sh",
    "cat {p}",
    'cat "{p}"',
    "cat '{p}'",
    "cat burn.sh",
    "sed -n 1p {p}",
    "sed -n '1p' {p}",
    'sed -n "1p" {p}',
    "sed -n 1p burn.sh",
    "awk '{{print}}' {p}",
    "head -n 1 {p}",
    "tail -n 1 {p}",
    'less "{p}"',
    "wc -l '{p}'",
    "diff '{p}' '{readme}'",
    "git diff -- {p}",
    'cp "{p}" {dest}',
    "mv '{p}' {dest}",
    'grep -nE "follow-through" {readme} | head -3',
    'grep "x" {readme}',
    "grep x README.md",
    "grep x '{readme}'",
    "git merge --ff-only && grep -nE 'follow-through' {readme} | head -3",
    "grep python {py}",
    "git log -S python3 -- {py}",
    'echo "python3 {py}"',
])
def test_reader_naming_load_file_allows(tmp_path, fmt):
    burn = tmp_path / "burn.sh"
    burn.write_text(BURN_SH)
    readme = tmp_path / "README.md"
    readme.write_text(README_DOCS)
    py = tmp_path / "burn.py"
    py.write_text(PY_LOADER)
    cmd = fmt.format(p=burn, readme=readme, py=py, dest=tmp_path / "copy.sh")
    r = run("terminal", {"command": cmd, "workdir": str(tmp_path)})
    assert r is None, (cmd, r)


# --- hub incident: 40 background `python -c "while True: pass"` loops, then pytest ----------
# The shell string is the command a worker submitted. These tests only pass it to the
# pre_tool_call hook. They do not start a process.

INCIDENT_40 = (
    'cd ~/code/_wt/flaky-t01a183; PY=~/.hermes/hermes-agent/.venv/bin/python; '
    'for i in $(seq 1 40); do (timeout 120 $PY -c "while True: pass" &) ; done; '
    'sleep 1; for i in 1 2 3; do $PY -m pytest -q -p no:cacheprovider '
    'tests/hermes_cli/test_config_rmw_lock.py '
    'tests/hermes_cli/test_update_stale_dashboard_review.py '
    'tests/tui_gateway/test_connector_accounts_rpc.py --durations=4 '
    '2>&1 | grep -E "s call|passed|failed|^E  " ; done; '
    'pkill -f "while True: pass"; true'
)
BOUNDED_PYTEST = (
    "cd ~/code/_wt/flaky-t01a183; PY=~/.hermes/hermes-agent/.venv/bin/python; "
    "$PY -m pytest -q -p no:cacheprovider "
    "tests/hermes_cli/test_config_rmw_lock.py "
    "tests/hermes_cli/test_update_stale_dashboard_review.py "
    "tests/tui_gateway/test_connector_accounts_rpc.py --durations=4"
)


def test_terminal_rejects_exact_40_loop_payload():
    r = run("terminal", {"command": INCIDENT_40, "timeout": 400})
    assert r and r["action"] == "block"
    assert "hermes-hub" in r["message"]
    assert "generate load on hermes-hub" not in r["message"].lower()
    r = run("terminal", {"command": INCIDENT_40, "background": True, "timeout": 120})
    assert r and r["action"] == "block"


def test_execute_code_rejects_exact_40_loop_payload():
    # Independent of the terminal test: the same payload inside execute_code, which
    # reaches the shell through hermes_tools.terminal() and is also scanned as code text.
    wrapped = "from hermes_tools import terminal\nterminal(" + repr(INCIDENT_40) + ", timeout=400)"
    r = run("execute_code", {"code": wrapped})
    assert r and r["action"] == "block", r
    r = run("execute_code", {"code": INCIDENT_40})
    assert r and r["action"] == "block", r


def test_bounded_pytest_allowed_on_both_paths():
    assert run("terminal", {"command": BOUNDED_PYTEST, "timeout": 400}) is None
    assert run("execute_code", {"code": "import subprocess\nsubprocess.run(['pytest', '-q', '-p', 'no:cacheprovider', 'tests/'])"}) is None
    assert run("execute_code", {"code": "from hermes_tools import terminal\nterminal(" + repr(BOUNDED_PYTEST) + ")"}) is None


def test_refusal_does_not_direct_saturation_at_hub():
    r = run("terminal", {"command": "stress-ng --cpu 16"})
    msg = r["message"]
    assert "hermes-hub" in msg
    assert "Generate load on hermes-hub" not in msg
    assert "stress-ng --cpu $(nproc) there" not in msg


def test_yaml_replaces_default_host_list(tmp_path, monkeypatch):
    monkeypatch.delenv("CPU_LOAD_GUARD_HOSTS")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cpu-load-guard.yaml").write_text("protected_hosts:\n  - only-this-host\n")
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "hermes-hub")
    assert not plugin.guard.enabled()
    (tmp_path / "cpu-load-guard.yaml").write_text("protected_hosts:\n  - hermes-hub\n")
    assert plugin.guard.enabled()
    assert run("terminal", {"command": INCIDENT_40})["action"] == "block"


def test_flat_list_replaces_default_without_pyyaml(tmp_path, monkeypatch):
    # The Hermes venv has no PyYAML. A missing module must not look like a missing file.
    monkeypatch.delenv("CPU_LOAD_GUARD_HOSTS")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cpu-load-guard.yaml").write_text(
        "# comment\nprotected_hosts:\n  - only-this-host\n"
    )
    real_import = __import__("builtins").__import__

    def _no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("yaml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_yaml)
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "hermes-hub")
    assert plugin.guard.protected_hosts() == ["only-this-host"]
    assert not plugin.guard.enabled()


# --- data files named in code: open("x.txt"), path = "x.txt" are not executed ----------------

def _data_txt(tmp_path):
    t = tmp_path / "call-spill.txt"
    t.write_text(BURN_SH)  # a dump that merely mentions a load generator
    return t


def test_exec_burn_sh_and_inline_still_block(tmp_path):
    p = tmp_path / "burn.sh"
    p.write_text(BURN_SH)
    for cmd in (f"bash {p}", f"cd {tmp_path} && bash burn.sh", f"nohup {p} &", f". {p}"):
        r = run("terminal", {"command": cmd, "workdir": str(tmp_path)})
        assert r and r["action"] == "block", cmd
    r = run("write_file", {"path": str(tmp_path / "burn2.sh"), "content": BURN_SH})
    assert r and r["action"] == "block"
    code = f'import subprocess\nsubprocess.run(["bash", "{p}"])\n'
    assert run("execute_code", {"code": code})["action"] == "block"


def test_execute_code_opening_data_file_allows(tmp_path):
    t = _data_txt(tmp_path)
    code = f'import json\npath = "{t}"\nwith open(path) as f: data = f.read()\n'
    assert run("execute_code", {"code": code}) is None
    assert run("execute_code", {"code": f'print(open("{t}").read())'}) is None
    assert run("execute_code", {"code": f'from pathlib import Path\nPath("{t}").read_text()'}) is None


def test_terminal_python_c_opening_data_file_allows(tmp_path):
    t = _data_txt(tmp_path)
    for cmd in (
        f"python3 -c 'print(open(\"{t}\").read())'",
        f"python3 -c \"path='{t}'; import json; d=open(path).read()\"",
    ):
        assert run("terminal", {"command": cmd, "workdir": str(tmp_path)}) is None, cmd


def test_write_file_of_py_reading_data_file_allows(tmp_path):
    t = _data_txt(tmp_path)
    content = f'src = "{t}"\ntext = open(src).read()\n'
    assert run("write_file", {"path": str(tmp_path / "show_ctx.py"), "content": content}) is None


@pytest.mark.parametrize("fmt", [
    "sudo {p}",
    "sudo -E {p}",
    "sudo -u root {p}",
    "cd {d} && sudo ./burn.sh",
    "env {p}",
    "env FOO=1 {p}",
    "env -i FOO=1 BAR=2 {p}",
])
def test_sudo_env_wrapped_argv0_blocks(tmp_path, fmt):
    p = tmp_path / "burn.sh"
    p.write_text(BURN_SH)
    p.chmod(0o755)
    r = run("terminal", {"command": fmt.format(p=p, d=tmp_path), "workdir": str(tmp_path)})
    assert r and r["action"] == "block", fmt


@pytest.mark.parametrize("fmt", ["sudo cat {t}", "env FOO=1 grep x {t}", "sudo -u root head {t}"])
def test_sudo_env_wrapped_reader_allows(tmp_path, fmt):
    t = _data_txt(tmp_path)
    assert run("terminal", {"command": fmt.format(t=t), "workdir": str(tmp_path)}) is None, fmt


@pytest.mark.parametrize("fmt", [
    "git bisect run {p}",
    "git bisect run bash {p}",
    "xargs {p}",
    "find . -name x -exec {p} {{}} \\;",
])
def test_exec_style_operand_blocks(tmp_path, fmt):
    p = tmp_path / "burn.sh"
    p.write_text(BURN_SH)
    p.chmod(0o755)
    r = run("terminal", {"command": fmt.format(p=p), "workdir": str(tmp_path)})
    assert r and r["action"] == "block", fmt


def test_spawn_argv_list_with_wide_cpu_count_blocks():
    s = "stress" + "-ng"
    for code in (
        f"import subprocess\nsubprocess.run(['{s}', '--cpu', '16', '--timeout', '1s'])\n",
        f"import subprocess\nsubprocess.Popen([\"{s}\", \"--cpu\", \"0\"])\n",
    ):
        r = run("execute_code", {"code": code})
        assert r and r["action"] == "block", code


def test_spawn_argv_list_with_small_cpu_count_allows():
    s = "stress" + "-ng"
    code = f"import subprocess\nsubprocess.run(['{s}', '--cpu', '2', '--timeout', '1s'])\n"
    assert run("execute_code", {"code": code}) is None

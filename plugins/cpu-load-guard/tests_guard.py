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


def test_default_protects_laptop(monkeypatch):
    monkeypatch.delenv("CPU_LOAD_GUARD_HOSTS")
    monkeypatch.setenv("HERMES_HOME", "/nonexistent")
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "David-MBP.local")
    assert plugin.guard.enabled()
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "hermes-hub")
    assert not plugin.guard.enabled()

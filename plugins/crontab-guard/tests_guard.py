"""Tests for crontab-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("crontab_guard_plugin", "__init__.py")


def blocked(tool, **args):
    return plugin._pre_tool_call(tool_name=tool, args=args) is not None


def test_blocks_whole_file_installs():
    for cmd in [
        "(crontab -l; echo '*/5 * * * * x') | crontab -",
        "crontab /tmp/new.cron",
        "crontab -r",
        "crontab -e",
        "/usr/bin/crontab -",
        "crontab -l > f && echo x >> f && crontab f",
        "sudo crontab -u root -",
    ]:
        assert blocked("terminal", command=cmd), cmd


def test_allows_reads_and_helper():
    for cmd in [
        "crontab -l",
        "crontab -l | grep vast",
        "/usr/bin/crontab -l; echo ok",
        "crontab -u daveey -l",
        "python3 ~/.hermes/plugins/crontab-guard/crontab_line.py add '*/30 * * * * x'",
        "cat my.crontab; ls crontab-backups",
        "echo hello",
    ]:
        assert not blocked("terminal", command=cmd), cmd


def test_fd_redirections_are_not_crontab_args():
    # `2>/dev/null` used to be read as a file argument "2", so a plain read was blocked.
    for cmd in [
        "crontab -l 2>/dev/null",
        "crontab -l 2>/dev/null | grep merge_queue | head -2",
        "crontab -l 2>&1 | head",
        "crontab -u daveey -l 2> /dev/null",
        "ssh mini 'crontab -l 2>/dev/null | grep x'",
    ]:
        assert not blocked("terminal", command=cmd), cmd
    for cmd in [
        "crontab 2>/dev/null",  # no args: installs from stdin
        "crontab -l 2>/dev/null | crontab -",
        "crontab /tmp/new.cron 2>/dev/null",
        "crontab -r 2>&1",
    ]:
        assert blocked("terminal", command=cmd), cmd


def test_prose_is_not_a_command():
    for cmd in [
        'git commit -m "fix: not crontab file args; edit crontab -r docs"',
        'echo "the crontab is fine"',
        "printf 'crontab -'",
        'echo "crontab -r; crontab /tmp/x"',
        "echo crontab -r",
        "printf '%s' '$(crontab -r)'",
        'bash -c \'echo "crontab -r"\'',
        "ssh host 'echo crontab -r'",
        "command -v crontab",
        "command -V crontab",
        "echo 'crontab -r' # crontab -r",
        'echo "\\$(crontab -r)"',
        "crontab '-l'",
        "crontab -l 2>/dev/null | echo ok",
    ]:
        assert not blocked("terminal", command=cmd), cmd


def test_command_positions_and_prefixes():
    for cmd in [
        "ssh host -p 22 crontab -r",
        "ssh host -t crontab -r",
        "ssh -o X=1 host -- crontab -r",
        "bash -o pipefail -c 'crontab -r'",
        "bash --norc -c 'crontab -r'",
        "env -S 'crontab -r'",
        "env --split-string='crontab -r'",
        "env -Scrontab -r",
        "{ crontab -r; }",
        "if true; then crontab -r; fi",
        "while false; do crontab -r; done",
        "! crontab -r",
        "time crontab -r",
        "eval 'crontab -r'",
        "dash -c 'crontab -r'",
        "ksh -c 'crontab -r'",
        "xargs -p crontab -r",
        "ssh mini crontab -r",
        "ssh -o ConnectTimeout=5 -i k host 'crontab -'",
        "ssh -p 22 -l user -J jump host 'crontab -'",
        "sudo -n -u root crontab -u root -",
        "bash -c 'crontab /tmp/x'",
        "sh -lc 'crontab -r'",
        "zsh -c 'crontab -'",
        "timeout 5 crontab -r",
        "timeout -s TERM -k 2 5 crontab -r",
        "(crontab -l; echo x) | crontab -",
        "env FOO=1 crontab -",
        "nice -n 5 nohup crontab -r",
        "exec command crontab -",
        "xargs -I {} crontab {}",
        "echo ok; crontab -r",
        "true && crontab -r",
        "false || crontab -r",
        "echo ok & crontab -r",
        "echo ok\ncrontab -r",
        'echo "$(crontab -r)"',
        'echo "`crontab -r`"',
        "echo $(echo $(crontab -r))",
    ]:
        assert blocked("terminal", command=cmd), cmd


def test_python_and_nested_calls():
    assert blocked("execute_code", code='subprocess.run(["crontab", "-"], input=t)')
    assert blocked("execute_code", code="os.system('crontab /tmp/x')")
    assert not blocked("execute_code", code='subprocess.run(["crontab", "-l"])')
    assert blocked("tool_call", calls=[{"name": "terminal", "arguments": {"command": "crontab -r"}}])
    assert not blocked("read_file", path="crontab")


def test_disable_env(monkeypatch):
    monkeypatch.setenv("CRONTAB_GUARD_DISABLE", "1")
    assert not blocked("terminal", command="crontab -r")


def test_claude_hook():
    hook = HERE / "claude_hook.py"
    bad = subprocess.run([sys.executable, hook], input='{"tool_input": {"command": "crontab -"}}',
                         capture_output=True, text=True)
    assert bad.returncode == 2 and "CRONTAB GUARD" in bad.stderr
    ok = subprocess.run([sys.executable, hook], input='{"tool_input": {"command": "crontab -l"}}',
                        capture_output=True, text=True)
    assert ok.returncode == 0
    import json
    for cmd, expected in [
        ('git commit -m "fix: not crontab file args; edit crontab -r docs"', 0),
        ('echo "the crontab is fine"', 0),
        ("printf 'crontab -'", 0),
        ("ssh -o ConnectTimeout=5 -i k host 'crontab -'", 2),
        ("bash -c 'crontab /tmp/x'", 2),
    ]:
        result = subprocess.run([sys.executable, hook],
                                input=json.dumps({"tool_input": {"command": cmd}}),
                                capture_output=True, text=True)
        assert result.returncode == expected, (cmd, result.stderr)


def test_line_helper(tmp_path):
    store = tmp_path / "tab"
    fake = tmp_path / "crontab"
    fake.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "-l" ]; then [ -f {store} ] && cat {store} || {{ echo "no crontab for x" >&2; exit 1; }}; '
        f"else cat > {store}; fi\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "CRONTAB_GUARD_BIN": str(fake), "CRONTAB_GUARD_STATE": str(tmp_path / "state")}

    def run(*a):
        return subprocess.run([sys.executable, HERE / "crontab_line.py", *a], env=env, capture_output=True, text=True)

    assert run("add", "*/30 * * * * a.sh").returncode == 0
    assert run("add", "0 * * * * b.sh").returncode == 0
    assert run("add", "0 * * * * b.sh").stdout.strip() == "crontab unchanged"
    assert store.read_text() == "*/30 * * * * a.sh\n0 * * * * b.sh\n"
    assert run("remove", "* *").returncode != 0  # ambiguous
    assert run("replace", "a.sh", "*/10 * * * * a.sh").returncode == 0
    assert run("remove", "b.sh").returncode == 0
    assert store.read_text() == "*/10 * * * * a.sh\n"
    assert len(list((tmp_path / "state/crontab-backups").glob("*.txt"))) == 4
    # restore re-adds b.sh from the newest backup without dropping a.sh
    assert run("restore").returncode == 0
    assert store.read_text() == "*/10 * * * * a.sh\n0 * * * * b.sh\n"

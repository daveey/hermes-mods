"""Tests for the tailnet terminal guard: the workload policy also covers ssh/scp/sftp/rsync run via `terminal`.
Run: uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider plugins/tailnet/tests_terminal_guard.py
"""

import importlib.util
import json
import os
import socket
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("tailnet_tg", HERE / "__init__.py")
tn = importlib.util.module_from_spec(spec)
sys.modules["tailnet_tg"] = tn
spec.loader.exec_module(tn)

POLICY = {"version": 1, "protected_addresses": ["192.0.2.10"],
          "protected_aliases": ["laptop.example"],
          "routing_message": "Route portable work to CI; macOS work to a dedicated build Mac."}
# `ssh -G <dest>` answers: an SSH-config alias whose HostName is the protected address, and one that is not.
SSH_CONFIG = {"buildmac": "192.0.2.10", "ci": "192.0.2.20"}
DNS = {"laptop.example": "192.0.2.10", "dns-alias.example": "192.0.2.10", "ci.example": "192.0.2.20"}

# Fake ssh: answers `-G` from SSH_CONFIG and records every other call (the guard must never make one).
FAKE_SSH = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if "-G" in a:
    dest = a[-1].rsplit("@", 1)[-1]
    forced = [o.split("=", 1)[1] for o in a if o.lower().startswith("hostname=")]
    print("user me")
    print("hostname " + (forced[-1] if forced else json.loads(os.environ["FAKE_SSH_CONFIG"]).get(dest, dest)))
    print("port 22")
    sys.exit(0)
open(os.environ["FAKE_STATE"] + "/ssh-ran.log", "a").write(json.dumps(a) + "\n")
'''


@pytest.fixture
def guard(tmp_path, monkeypatch):
    home = tmp_path / "home"
    policy = home / ".hermes" / "tailnet" / "workload-policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps(POLICY))
    monkeypatch.setenv("HOME", str(home))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_CONFIG", json.dumps(SSH_CONFIG))
    monkeypatch.setenv("FAKE_STATE", str(tmp_path))
    for k in ("MAC_HEAVY_OK", "MAC_HEAVY_GUARD"):
        monkeypatch.delenv(k, raising=False)

    def resolve(host, port, *a, **kw):  # no real DNS from this suite
        if host not in DNS:
            raise socket.gaierror("test DNS: unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DNS[host], port or 0))]
    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def run(command, tool="terminal"):
        return tn.pre_tool_call(tool_name=tool, args={"command": command})
    run.policy = policy
    run.ran = lambda: (tmp_path / "ssh-ran.log").exists()
    return run


def assert_blocked(result, reason=""):
    assert result and result["action"] == "block", result
    assert result["message"].startswith("tailnet workload policy: "), result
    assert POLICY["routing_message"] in result["message"], result
    assert reason in result["message"], result


# ---- the spec's cases

def test_ssh_user_at_protected_alias_tar_blocked(guard):
    assert_blocked(guard("ssh -o BatchMode=yes me@laptop.example 'tar czf /tmp/x.tgz ~/logs'"), "tailnet_copy")


def test_ssh_alias_whose_config_hostname_is_protected_blocked(guard):
    assert_blocked(guard("ssh buildmac 'xcodebuild test'"), "xcodebuild")


def test_ssh_heredoc_with_build_blocked(guard):
    cmd = "ssh me@laptop.example bash -s <<'EOF'\ncd repo\ncargo build\nEOF\necho done"
    assert_blocked(guard(cmd), "cargo build")


def test_scp_to_protected_blocked(guard):
    assert_blocked(guard("scp f laptop.example:"), "tailnet_copy")


def test_ssh_protected_hostname_allowed(guard):
    assert guard("ssh me@laptop.example hostname") is None
    assert guard("ssh -o BatchMode=yes buildmac hostname") is None


def test_ssh_unprotected_build_allowed(guard):
    assert guard("ssh ci 'cargo build'") is None
    assert guard("ssh me@ci.example 'cargo build'") is None


# ---- more routes to the same machine

@pytest.mark.parametrize("command", [
    "ssh me@192.0.2.10 'swift build'",                       # protected address literal
    "ssh dns-alias.example 'cargo test'",                    # resolves to a protected address
    "ssh -p 22 -i ~/.ssh/k LAPTOP.EXAMPLE. 'xcodebuild'",     # spelling, options with values
    "ssh -oBatchMode=yes laptop.example cargo build",         # glued option, unquoted remote command
    "ssh ssh://me@laptop.example 'cargo build'",
    "cd /tmp && ssh laptop.example 'cargo build' | tail -5",  # compound
    "bash -lc \"ssh laptop.example 'cargo build'\"",          # shell payload
    "timeout 60 env X=1 ssh laptop.example 'cargo build'",   # wrappers
    "out=$(ssh laptop.example 'cargo build')",                # command substitution
    "ssh -o HostName=192.0.2.10 innocuous 'cargo build'",     # -o HostName redirect via ssh -G
    "ssh laptop.example bash -lc 'xcodebuild test'",
    "ssh laptop.example 'cp -R src dst'",
    "ssh laptop.example <<'EOF'\ncargo build\nEOF",           # login shell reads the heredoc
    "ssh laptop.example sh <<EOF\nswift test\nEOF",
])
def test_protected_heavy_routes_blocked(guard, command):
    assert_blocked(guard(command))


@pytest.mark.parametrize("command,reason", [
    ("ssh laptop.example", "interactive"),
    ("ssh -t me@laptop.example", "interactive"),
    ("ssh laptop.example < build.sh", "interactive"),
    ("cat build.sh | ssh laptop.example bash -s", "stdin"),
    ("ssh laptop.example bash <<< 'cargo build'", "stdin"),
    ("ssh laptop.example \"cargo build '\"", "cannot classify"),
    ("ssh -s laptop.example sftp", "tailnet_copy"),
])
def test_unclassifiable_protected_ssh_fails_closed(guard, command, reason):
    assert_blocked(guard(command), reason)


@pytest.mark.parametrize("command", [
    "scp laptop.example:/tmp/a .", "scp -P 22 f me@192.0.2.10:/tmp/", "scp -r dir buildmac:",
    "rsync -a dir/ laptop.example:dir/", "rsync -e 'ssh -p 22' dir/ me@dns-alias.example:/x",
    "rsync -av laptop.example::module/ .", "sftp laptop.example", "sftp me@192.0.2.10:/tmp",
    "scp scp://me@laptop.example//tmp/a .",
])
def test_transfers_with_protected_end_blocked(guard, command):
    assert_blocked(guard(command), "tailnet_copy")


@pytest.mark.parametrize("command", [
    "ssh me@laptop.example 'df -h; uptime'", "ssh laptop.example bash -s <<'EOF'\nhostname\nEOF",
    "ssh laptop.example 'cp ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak && grep -c ssh ~/.ssh/authorized_keys'",
    "ssh -N -L 8080:localhost:80 laptop.example", "ssh -G laptop.example", "ssh -V",
    "scp f ci.example:/tmp/", "rsync -a dir/ ci:dir/", "rsync -a src/ dst/", "scp a b", "sftp ci",
    "echo ssh laptop.example cargo build",  # text, not a command
    "grep -r scp notes.txt", "ssh unknown.example 'cargo build'",
])
def test_light_or_unprotected_allowed(guard, command):
    assert guard(command) is None


def test_no_policy_is_noop(guard):
    guard.policy.unlink()
    assert guard("ssh laptop.example 'cargo build'") is None
    assert guard("scp f laptop.example:") is None


def test_malformed_policy_fails_closed_for_remote_commands_only(guard):
    guard.policy.write_text("{")
    assert_result = guard("ssh ci hostname")
    assert assert_result and "invalid workload policy" in assert_result["message"]
    assert guard("ls -la") is None


def test_batched_tool_call_is_checked(guard):
    result = tn.pre_tool_call(tool_name="tool_call", args={"calls": [
        {"name": "terminal", "arguments": {"command": "ssh laptop.example 'cargo build'"}}]})
    assert_blocked(result)


def test_other_tools_and_guard_never_runs_ssh(guard):
    assert tn.pre_tool_call(tool_name="read_file", args={"path": "ssh"}) is None
    assert_blocked(guard("ssh buildmac 'xcodebuild test'"))
    assert not guard.ran()  # only `ssh -G` (config print) was invoked


def test_register_adds_hook_even_when_tools_disabled(guard, monkeypatch):
    hooks, tools = [], []

    class Ctx:
        def register_hook(self, name, fn):
            hooks.append(name)

        def register_tool(self, **kw):
            tools.append(kw["name"])
    monkeypatch.setattr(tn, "settings", lambda: {"enabled": False})
    tn.register(Ctx())
    assert hooks == ["pre_tool_call"] and tools == []

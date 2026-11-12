"""worker_ssh_refused (agent-9a9552bdc6): a refused hub-to-worker ssh must point at the SSM route, not read as
"unreachable".

2026-11-12 a hub agent tried `ssh hermes-worker-N` on all six workers (publickey denied, host-key failures), told
David it "couldn't reach" them and handed the change to a card; the next turn it changed all six through AWS SSM in
minutes. The hub has no admin SSH to workers by design. A terminal ssh to a worker (names/IPs read from the hub's
`~/.ssh/hermes-workers.json` ledger, so no hostnames live in this public repo) whose output shows an ssh auth or
host-key refusal gets one hint naming `hub-worker-exec`.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rules = load("coach_rules_test_wsr", ROOT / "agent_rules.py")
core = load("coach_core_test_wsr", ROOT / "core.py")
plugin = load("coach_plugin_test_wsr", ROOT / "__init__.py")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def coach(tmp_path):
    return rules.AgentCoach(None, signals=core.Store(tmp_path / "coach"), clock=Clock())


def term(c, cmd, output, exit_code=255, sid="s1"):
    c.observe(sid, "terminal", {"command": cmd}, json.dumps({"output": output, "exit_code": exit_code}))


# The hub's reach-keys ledger shape (hosts/hermes-workers/reach-keys.py): one record per worker, plus a
# `#hub-admin` record for the write grant. TEST-NET addresses stand in for the tailnet IPs.
LEDGER = {
    "worker-1": {"ips": ["192.0.2.11"], "key": "ssh-ed25519 AAAA"},
    "worker-2": {"ips": ["192.0.2.12"], "key": "ssh-ed25519 AAAB"},
    "worker-2#hub-admin": {"ips": ["192.0.2.12"], "key": "ssh-ed25519 AAAC"},
    "worker-6": {"ips": ["192.0.2.16"], "key": "ssh-ed25519 AAAD"},
}

# Replay of the incident call (msg 1244092): a loop over the workers by name and an old tailnet IP, each refused.
INCIDENT_CMD = ("for h in hermes-worker-1 hermes-worker-2 192.0.2.16; do echo \"== $h\"; "
                "ssh -o BatchMode=yes -o ConnectTimeout=8 ubuntu@$h 'hostname; systemctl --user is-active "
                "hermes-gateway' 2>&1 | tail -3; done")
INCIDENT_OUT = ("== hermes-worker-1\nubuntu@hermes-worker-1: Permission denied (publickey).\n"
                "== hermes-worker-2\nHost key verification failed.\n"
                "== 192.0.2.16\nNo ED25519 host key is known for 192.0.2.16 and you have requested strict "
                "checking.\nHost key verification failed.\n")


@pytest.fixture
def on_hub(tmp_path, monkeypatch):
    ledger = tmp_path / "ssh" / "hermes-workers.json"
    ledger.parent.mkdir()
    ledger.write_text(json.dumps(LEDGER))
    monkeypatch.setattr(rules, "_self_host", lambda: "hermes-hub")
    monkeypatch.setattr(rules, "_worker_ledger_path", lambda: ledger, raising=False)
    live = plugin._load("agent_rules")  # the module the built plugin's hooks use
    monkeypatch.setattr(live, "_self_host", lambda: "hermes-hub")
    monkeypatch.setattr(live, "_worker_ledger_path", lambda: ledger, raising=False)
    return ledger


def test_incident_replay_gets_the_ssm_hint(tmp_path, on_hub):
    c = coach(tmp_path)
    term(c, INCIDENT_CMD, INCIDENT_OUT, exit_code=0)
    hint = c.take("s1") or ""
    assert "hub-worker-exec" in hint, hint
    assert "SSM" in hint and "unreachable" in hint
    term(c, INCIDENT_CMD, INCIDENT_OUT, exit_code=0)
    assert c.take("s1") is None  # once per session


@pytest.mark.parametrize("cmd, out", [
    ("ssh hermes-worker-2 true", "hermes-worker-2: Permission denied (publickey)."),
    ("ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=yes ubuntu@192.0.2.12 uptime",
     "No ED25519 host key is known for 192.0.2.12 and you have requested strict checking.\n"
     "Host key verification failed."),
    ("ssh worker-6.tail1234.ts.net 'hermes --version'", "Host key verification failed."),
    ("for n in 1 2 6; do ssh -o BatchMode=yes hermes-worker-$n hostname; done",
     "hermes-worker-1: Permission denied (publickey)."),
])
def test_fires_on_each_refusal_shape(tmp_path, on_hub, cmd, out):
    c = coach(tmp_path)
    term(c, cmd, out)
    assert "hub-worker-exec" in (c.take("s1") or "")


def test_quiet_on_successful_ssh(tmp_path, on_hub):
    c = coach(tmp_path)
    term(c, "ssh -o UserKnownHostsFile=~/.ssh/known_hosts_hermes_workers hermes-worker-2 hostname",
         "hermes-worker-2\n", exit_code=0)
    assert c.take("s1") is None


def test_quiet_on_ssh_to_a_non_worker_host(tmp_path, on_hub):
    c = coach(tmp_path)
    term(c, "ssh ubuntu@build-box-7 true", "ubuntu@build-box-7: Permission denied (publickey).")
    term(c, "ssh ubuntu@192.0.2.99 true", "Host key verification failed.")
    term(c, "ssh hermes-worker-9 true", "hermes-worker-9: Permission denied (publickey).")  # not in the ledger
    assert c.take("s1") is None


def test_quiet_when_not_on_the_hub(tmp_path, on_hub, monkeypatch):
    monkeypatch.setattr(rules, "_self_host", lambda: "laptop-1")
    c = coach(tmp_path)
    term(c, "ssh hermes-worker-2 true", "hermes-worker-2: Permission denied (publickey).")
    assert c.take("s1") is None


def test_quiet_without_a_ledger(tmp_path, on_hub):
    on_hub.unlink()
    c = coach(tmp_path)
    term(c, "ssh hermes-worker-2 true", "hermes-worker-2: Permission denied (publickey).")
    assert c.take("s1") is None


def test_quiet_when_refusal_text_is_only_in_the_command(tmp_path, on_hub):
    c = coach(tmp_path)
    term(c, "grep -c 'Permission denied (publickey)' /var/log/auth.log; ssh hermes-worker-1 true", "3\n", 0)
    assert c.take("s1") is None


def test_hint_lands_on_the_refused_call_via_transform(tmp_path, on_hub):
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"))
    args = {"command": "ssh hermes-worker-2 true"}
    res = json.dumps({"output": "hermes-worker-2: Permission denied (publickey).", "exit_code": 255})
    out = hooks["transform_tool_result"](tool_name="terminal", args=args, result=res, session_id="s1")
    assert out and "hub-worker-exec" in out
    hooks["post_tool_call"](tool_name="terminal", args=args, result=res, session_id="s1")
    nxt = hooks["transform_tool_result"](tool_name="terminal", args={"command": "ls"},
                                         result=json.dumps({"output": "x", "exit_code": 0}), session_id="s1")
    assert nxt is None

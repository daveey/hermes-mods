"""Read-only capability discovery must survive a stale broker executable."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def core():
    spec = importlib.util.spec_from_file_location("broker_core", Path(__file__).resolve().parents[1] / "core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def broker(path, output, code=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n[ \"$#\" = 1 ] && [ \"$1\" = scopes ] || exit 99\nprintf '%s' '" + output + "'\nexit " + str(code) + "\n")
    path.chmod(0o700)
    return str(path)


def test_current_local_broker_precedes_stale_path(core, tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    broker(tmp_path / ".local/bin/token-broker", json.dumps([
        {"name": "user/cf-token-minter", "tier": "auto", "env": {"TOKEN": "must-not-leak"}},
    ]))
    stale = broker(tmp_path / "old/token-broker", '{"Message":null}', 1)
    monkeypatch.setattr(core.shutil, "which", lambda _: stale)
    assert core.broker_scopes() == ["user/cf-token-minter (auto)"]


@pytest.mark.parametrize("output,code", [('{"error":"unavailable"}', 0), ('[]', 1), ('not-json', 0)])
def test_invalid_local_response_falls_back(core, tmp_path, monkeypatch, output, code):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    broker(tmp_path / ".local/bin/token-broker", output, code)
    alternate = broker(tmp_path / "alternate/token-broker", '[{"name":"user/service","tier":"approval"}]')
    monkeypatch.setattr(core.shutil, "which", lambda _: alternate)
    assert core.broker_scopes() == ["user/service (approval)"]


@pytest.mark.parametrize("catalog", ['[]', '[null, {"name":42}]'])
def test_successful_empty_catalog_is_authoritative(core, tmp_path, monkeypatch, catalog):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    broker(tmp_path / ".local/bin/token-broker", catalog)
    alternate = broker(tmp_path / "alternate/token-broker", '[{"name":"stale","tier":"auto"}]')
    monkeypatch.setattr(core.shutil, "which", lambda _: alternate)
    assert core.broker_scopes() == []


def test_failed_exit_cannot_advertise_capability(core, tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    path = broker(tmp_path / ".local/bin/token-broker", '[{"name":"stale","tier":"auto"}]', 1)
    monkeypatch.setattr(core.shutil, "which", lambda _: path)
    assert core.broker_scopes() == []


def test_only_valid_metadata_is_exposed(core, tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    path = broker(tmp_path / ".local/bin/token-broker", json.dumps([
        {"name": {"TOKEN": "must-not-leak"}, "tier": "auto"},
        {"name": "user/good", "tier": {"TOKEN": "must-not-leak"}},
        {"name": "user/valid", "tier": "auto", "env": {"TOKEN": "must-not-leak"}},
    ]))
    monkeypatch.setattr(core.shutil, "which", lambda _: path)
    assert core.broker_scopes() == ["user/good (?)", "user/valid (auto)"]


@pytest.mark.parametrize("error", [OSError("resolve failed"), RuntimeError("symlink loop")])
def test_resolution_failure_falls_back(core, tmp_path, monkeypatch, error):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    original = core.Path.resolve
    def resolve(path, *args, **kwargs):
        if path.parent.name == "bin":
            raise error
        return original(path, *args, **kwargs)
    alternate = broker(tmp_path / "alternate/token-broker", '[{"name":"user/service","tier":"auto"}]')
    monkeypatch.setattr(core.shutil, "which", lambda _: alternate)
    monkeypatch.setattr(core.Path, "resolve", resolve)
    assert core.broker_scopes() == ["user/service (auto)"]


@pytest.mark.parametrize("state", ["missing", "not-executable", "timeout"])
def test_unusable_local_client_falls_back(core, tmp_path, monkeypatch, state):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    local = tmp_path / ".local/bin/token-broker"
    if state == "not-executable":
        broker(local, '[]')
        local.chmod(0o600)
    alternate = broker(tmp_path / "alternate/token-broker", '[{"name":"user/service","tier":"auto"}]')
    monkeypatch.setattr(core.shutil, "which", lambda _: alternate)
    original = core.subprocess.run
    def run(argv, **kwargs):
        assert argv[1:] == ["scopes"]
        if state == "timeout" and argv[0] == str(local):
            raise core.subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return original(argv, **kwargs)
    monkeypatch.setattr(core.subprocess, "run", run)
    assert core.broker_scopes() == ["user/service (auto)"]


def test_aliases_not_retried(core, tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    local = tmp_path / ".local/bin/token-broker"
    broker(local, '[]', 1)
    alias = tmp_path / "alias"
    alias.symlink_to(local)
    monkeypatch.setattr(core.shutil, "which", lambda _: str(alias))
    calls = []
    original = core.subprocess.run
    def run(argv, **kwargs):
        calls.append(argv)
        return original(argv, **kwargs)
    monkeypatch.setattr(core.subprocess, "run", run)
    assert core.broker_scopes() == []
    assert calls == [[str(local), "scopes"]]


def test_broker_fallback_reaches_render_gate(core, gate, tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    broker(tmp_path / ".local/bin/token-broker", '[{"name":"user/cf-token-minter","tier":"auto"}]')
    stale = broker(tmp_path / "old/token-broker", '{"Message":null}', 1)
    monkeypatch.setattr(core.shutil, "which", lambda _: stale)
    monkeypatch.setattr(core, "ssh_hosts", lambda: [])
    monkeypatch.setattr(core, "chrome_accounts", lambda: [])
    monkeypatch.setattr(core, "vault_entries", lambda: [])
    monkeypatch.setattr(gate.core, "capability_sheet", core.capability_sheet)
    prompts = []
    def model(system, user):
        prompts.append(user)
        return json.dumps({"asks": [{"i": 0, "verdict": "agent_can_do", "confidence": 0.99,
            "reason": "Use existing minter capability", "how": "token-broker exec --scope user/cf-token-minter"}]})
    gate.LLM = model
    ask = "Could you configure the Cloudflare DNS record?"
    result = gate.pre_final_gate(session_id="s1", final_response=ask, attempt=100)
    assert prompts and "user/cf-token-minter (auto)" in prompts[0]
    assert result and result.get("replace") and ask not in result["replace"]


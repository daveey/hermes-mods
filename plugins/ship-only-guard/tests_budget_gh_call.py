"""Exercise GitHub subprocess deadlines without any real GitHub requests."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def guard(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "ship_budget_gh_call_guard", Path(__file__).with_name("guard.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    # Every subprocess is replaced before any production entry point is called.
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: pytest.fail("unexpected subprocess"))
    return g


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_gh_call_expired_payload_never_spawns(guard, monkeypatch, tmp_path, method):
    g = guard
    monkeypatch.setattr(g.time, "monotonic", lambda: 101.0)
    calls = []
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: calls.append((a, k)) or
                        SimpleNamespace(returncode=0, stdout='{"ok": true}'))
    token = g._PREFLIGHT_DEADLINE.set(100.0)
    try:
        with pytest.raises(g._PreflightExpired):
            g._gh_call(["-X", method, "repos/example/project/git/refs"],
                       tmp_path, {}, payload={"sha": "a" * 40})
    finally:
        g._PREFLIGHT_DEADLINE.reset(token)
        assert calls == [], "expired GitHub call reached subprocess.run"


@pytest.mark.parametrize("remaining,expected", [(1.0, 1.0), (30.0, 10.0), (None, 10.0)])
def test_gh_call_payload_timeout_and_response(guard, monkeypatch, tmp_path, remaining, expected):
    g = guard
    monkeypatch.setattr(g.time, "monotonic", lambda: 100.0)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"sha": "created"}')
    monkeypatch.setattr(g.subprocess, "run", run)
    token = g._PREFLIGHT_DEADLINE.set(None if remaining is None else 100.0 + remaining)
    env = {"FIXTURE": "yes"}
    payload = {"ref": "refs/heads/fixture", "sha": "a" * 40}
    try:
        assert g._gh_call(["repos/example/project/git/refs"], tmp_path, env,
                          payload=payload) == (0, {"sha": "created"})
    finally:
        g._PREFLIGHT_DEADLINE.reset(token)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["gh", "api", "repos/example/project/git/refs", "--input", "-"]
    assert kwargs["timeout"] == expected
    assert json.loads(kwargs["input"]) == payload
    assert kwargs["env"] == {"FIXTURE": "yes", "GH_PROMPT_DISABLED": "1"}
    assert env == {"FIXTURE": "yes"}


@pytest.mark.parametrize("expired", [False, True])
def test_turn_acquire_never_writes_after_preflight_expiry(guard, monkeypatch, tmp_path, expired):
    g = guard
    clock = [100.0]
    monkeypatch.setattr(g.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(g, "_now", lambda: 1_800_000_000)
    calls = []
    head = "a" * 40
    tree = "b" * 40
    made = "c" * 40
    def run(argv, **kwargs):
        method = argv[argv.index("-X") + 1] if "-X" in argv else (
            "POST" if kwargs.get("input") is not None else "GET")
        endpoint = next(arg for arg in argv if arg.startswith("repos/"))
        calls.append((method, endpoint, clock[0]))
        if method == "GET" and "/git/ref/" in endpoint:
            return SimpleNamespace(returncode=1, stdout="")
        if method == "GET" and endpoint.endswith(head):
            # Exhaust the aggregate budget during a successful read, well before
            # the turn's separate elapsed-time budget could stop the next write.
            if expired:
                clock[0] = 118.0
            return SimpleNamespace(returncode=0, stdout=json.dumps({"tree": {"sha": tree}}))
        if method == "POST" and endpoint.endswith("/commits"):
            return SimpleNamespace(returncode=0, stdout=json.dumps({"sha": made}))
        if method == "POST" and endpoint.endswith("/refs"):
            return SimpleNamespace(returncode=1, stdout="")  # exercise PATCH fallback
        if method == "PATCH":
            return SimpleNamespace(returncode=0, stdout="{}")
        pytest.fail(f"unexpected fixture request: {method} {endpoint}")
    monkeypatch.setattr(g.subprocess, "run", run)
    # Isolate the existing write-boundary regression from protocol-2 ordering reads.
    monkeypatch.setattr(g, "_turn_hold_refusal", lambda *a: None)
    token = g._PREFLIGHT_DEADLINE.set(118.0)
    try:
        result = g.bounded_preflight(lambda: g._turn_safe(
            g._turn_acquire, 12, {"headRefOid": head, "files": [{"path": "fixture.py"}]},
            "example/project", tmp_path, {}))
    finally:
        g._PREFLIGHT_DEADLINE.reset(token)
    writes = [call for call in calls if call[0] in {"POST", "PATCH", "DELETE"}]
    assert [call[0] for call in calls[:2]] == ["GET", "GET"]
    if expired:
        # A final refusal alone is insufficient: _turn_safe absorbs exceptions,
        # and a bypassed _gh_call can already have performed all three writes.
        assert writes == [], "land-turn writes escaped after the aggregate deadline"
        assert "deadline exceeded" in result
    else:
        assert [call[0] for call in writes] == ["POST", "POST", "PATCH"]
        assert "UTC" in result

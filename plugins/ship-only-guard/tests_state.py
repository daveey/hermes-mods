"""State-branch classifications only: never execute a merge."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("state_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
SHA = "a" * 40
CMD = ["gh", "pr", "merge", "12", "--repo", "example/protected", "--rebase", "--match-head-commit", SHA]


APPROVAL = {"id": "PRR_x", "author": {"login": "you"}, "authorAssociation": "MEMBER", "state": "COMMENTED",
            "submittedAt": "2026-11-13T15:00:00Z", "commit": {"oid": SHA},
            "body": f"Independent exact-head review @ {SHA}: approve"}


def payload(**updates):
    return {"number": 12, "state": "OPEN", "baseRefName": "state", "headRefOid": SHA,
            "url": "https://github.com/example/protected/pull/12", "reviews": [APPROVAL], "comments": [],
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}], **updates}


def check(argv=CMD):
    return g.check_argv(argv, repos={"example/protected"}, env={"GH_REPO": "example/protected"})


def probe(monkeypatch, data):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    monkeypatch.setattr(g.subprocess, "run", run)
    return calls


def test_state_exact_head_allowed(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert check() is None
    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv[:6] == ["gh", "pr", "view", "12", "--repo", "example/protected"]
    assert kw["timeout"] <= 20 and not kw.get("shell")


def test_state_without_exact_head_approval_refused(monkeypatch):
    # #2413: green rollup, right base and head, zero reviews and comments. The preflight used to admit it.
    probe(monkeypatch, payload(reviews=[]))
    assert "no approval of its exact head" in check()
    probe(monkeypatch, payload(reviews=[dict(APPROVAL, commit={"oid": "b" * 40})]))
    assert "no approval of its exact head" in check()


@pytest.mark.parametrize("updates", [
    {"baseRefName": "main"}, {"baseRefName": "other"}, {"baseRefName": None},
    {"headRefOid": "b" * 40}, {"state": "CLOSED"}, {"state": "MERGED"},
    {"number": 13}, {"url": "https://github.com/example/other/pull/12"},
    {"statusCheckRollup": []}, {"statusCheckRollup": None},
    {"statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": "SUCCESS"}]},
    {"statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]},
    {"statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"}]},
])
def test_uncertain_or_wrong_target_refused(monkeypatch, updates):
    probe(monkeypatch, payload(**updates))
    assert check()


@pytest.mark.parametrize("data", [None, [], {}, "bad", {"number": 12}])
def test_bad_response_refused(monkeypatch, data):
    probe(monkeypatch, data)
    assert check()


@pytest.mark.parametrize("extra", [["--auto"], ["--admin"], ["--squash"], ["--delete-branch"], ["--unknown"], ["--repo", "example/protected"]])
def test_noncanonical_merge_refused_without_probe(monkeypatch, extra):
    calls = probe(monkeypatch, payload())
    assert check(CMD + extra)
    assert not calls


def test_missing_pin_or_explicit_repo_refused(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert check(CMD[:-2])
    assert check(CMD[:-1] + ["a" * 12])
    assert check(CMD[:4] + CMD[6:])
    assert not calls


def test_transport_failure_refused(monkeypatch):
    for error in (OSError("offline"), g.subprocess.TimeoutExpired("gh", 15)):
        def run(*args, **kwargs):
            raise error
        monkeypatch.setattr(g.subprocess, "run", run)
        assert check()
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert check()
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="not json"))
    assert check()


def test_legacy_status_and_bounded_rollup(monkeypatch):
    probe(monkeypatch, payload(statusCheckRollup=[{"__typename": "StatusContext", "state": "SUCCESS"}]))
    assert check() is None
    probe(monkeypatch, payload(statusCheckRollup=payload()["statusCheckRollup"] * 100))
    assert check()


def test_shell_python_and_tool_state_route(monkeypatch):
    import shlex
    probe(monkeypatch, payload())
    command = shlex.join(CMD)
    assert g.check_shell(command, repos={"example/protected"}, env={}) is None
    assert g.check_python(f"subprocess.run({CMD!r})", repos={"example/protected"}, env={}) is None
    assert g.check_tool("terminal", {"command": command}, repos={"example/protected"}, env={}) is None
    probe(monkeypatch, payload(baseRefName="main"))
    assert g.check_tool("terminal", {"command": command}, repos={"example/protected"}, env={})


def test_merge_api_stays_refused(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert check(["gh", "api", "repos/example/protected/pulls/12/merge"])
    assert not calls

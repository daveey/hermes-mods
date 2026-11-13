"""Upstream-sync merge classifications only: never execute a merge."""
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("sync_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
SHA = "c" * 40
CMD = ["gh", "pr", "merge", "12", "-R", "example/protected", "--merge", "--match-head-commit", SHA]
GATE = CMD + ["--subject", f"Merge tested upstream sync {SHA[:10]} into main",
              "--body", "Full suite: https://example.com/run/1\nAll required checks pass (v2): success"]
REQUIRED = {"__typename": "CheckRun", "name": "All required checks pass (v2)",
            "status": "COMPLETED", "conclusion": "SUCCESS"}
OPTIONAL_RED = {"__typename": "CheckRun", "name": "pinned-source-validate",
                "status": "COMPLETED", "conclusion": "FAILURE"}


def payload(**updates):
    return {"number": 12, "state": "OPEN", "baseRefName": "main", "headRefName": f"sync/upstream-{SHA}",
            "headRefOid": SHA, "isCrossRepository": False,
            "url": "https://github.com/example/protected/pull/12",
            "statusCheckRollup": [OPTIONAL_RED, REQUIRED], **updates}


def check(argv=CMD):
    return g.check_argv(argv, repos={"example/protected"}, env={"GH_REPO": "example/protected"})


def probe(monkeypatch, data):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    monkeypatch.setattr(g.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("argv", [CMD, GATE, CMD[:4] + ["--repo"] + CMD[5:]])
def test_exact_sync_merge_allowed_despite_red_optional_check(monkeypatch, argv):
    calls = probe(monkeypatch, payload())
    assert check(argv) is None
    assert len(calls) == 1
    pargv, kw = calls[0]
    assert pargv[:6] == ["gh", "pr", "view", "12", "--repo", "example/protected"]
    assert "headRefName" in pargv[-1] and "statusCheckRollup" in pargv[-1]
    assert kw["timeout"] <= 20 and not kw.get("shell")


def test_legacy_status_context_required_check(monkeypatch):
    probe(monkeypatch, payload(statusCheckRollup=[
        {"__typename": "StatusContext", "context": "All required checks pass (v2)", "state": "SUCCESS"}]))
    assert check() is None


@pytest.mark.parametrize("updates", [
    {"headRefName": "sync/upstream-" + "d" * 40}, {"headRefName": "feature/x"}, {"headRefName": SHA},
    {"headRefName": None}, {"headRefOid": "d" * 40}, {"baseRefName": "state"}, {"baseRefName": None},
    {"state": "CLOSED"}, {"state": "MERGED"}, {"number": 13}, {"isCrossRepository": True},
    {"isCrossRepository": None}, {"url": "https://github.com/example/other/pull/12"},
    {"statusCheckRollup": []}, {"statusCheckRollup": None}, {"statusCheckRollup": [OPTIONAL_RED]},
    {"statusCheckRollup": [{**REQUIRED, "conclusion": "FAILURE"}]},
    {"statusCheckRollup": [{**REQUIRED, "status": "IN_PROGRESS", "conclusion": None}]},
    {"statusCheckRollup": [{**REQUIRED, "conclusion": "SKIPPED"}]},
    {"statusCheckRollup": [REQUIRED, {**REQUIRED, "conclusion": "FAILURE"}]},
    {"statusCheckRollup": [{**REQUIRED, "__typename": None}]},
    {"statusCheckRollup": [REQUIRED, "bad"]},
])
def test_wrong_target_or_required_check_refused(monkeypatch, updates):
    probe(monkeypatch, payload(**updates))
    assert check()


@pytest.mark.parametrize("data", [None, [], {}, "bad", {"number": 12}])
def test_bad_response_refused(monkeypatch, data):
    probe(monkeypatch, data)
    assert check()


@pytest.mark.parametrize("extra", [["--admin"], ["--auto"], ["--squash"], ["--rebase"], ["--merge"],
                                   ["--delete-branch"], ["--unknown"], ["--body-file", "x"],
                                   ["--repo", "example/protected"], ["--subject", "a", "--subject", "b"],
                                   ["13"]])
def test_noncanonical_merge_refused_without_probe(monkeypatch, extra):
    calls = probe(monkeypatch, payload())
    assert check(CMD + extra)
    assert not calls


def test_missing_pin_mode_or_explicit_repo_refused(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert check(CMD[:-2])                       # no head pin
    assert check(CMD[:-1] + [SHA[:12]])          # short sha
    assert check(CMD[:-1] + [SHA.upper()])       # non-canonical sha
    assert check(CMD[:4] + CMD[6:])              # implicit repo
    assert check(CMD[:6] + CMD[7:])              # no merge mode
    assert check(CMD[:3] + ["https://github.com/example/protected/pull/12"] + CMD[4:])
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


def test_shell_python_and_tool_route(monkeypatch):
    probe(monkeypatch, payload())
    command = shlex.join(GATE)
    repos = {"example/protected"}
    assert g.check_shell(command, repos=repos, env={}) is None
    assert g.check_python(f"subprocess.run({GATE!r})", repos=repos, env={}) is None
    assert g.check_tool("terminal", {"command": command}, repos=repos, env={}) is None
    probe(monkeypatch, payload(headRefName="topic"))
    assert g.check_tool("terminal", {"command": command}, repos=repos, env={})


def test_cd_unset_var_prefix_still_probes(monkeypatch, tmp_path):
    # Like the state route (t_01a1d625bbebeaa8): an unexpanded `cd $VAR;` target must not become the probe's
    # cwd, where real subprocess.run raises and the green sync PR would be refused for no reason.
    calls = []
    def run(argv, **kwargs):
        calls.append(kwargs)
        if kwargs.get("cwd") is not None and not g.os.path.isdir(kwargs["cwd"]):
            raise FileNotFoundError(2, "No such file or directory", kwargs["cwd"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload()))
    monkeypatch.setattr(g.subprocess, "run", run)
    command = "cd $HERMES_KANBAN_WORKSPACE; " + shlex.join(GATE)
    assert g.check_shell(command, cwd=str(tmp_path), repos={"example/protected"}, env={}) is None
    assert len(calls) == 1 and calls[0]["cwd"] is None


def test_state_route_unchanged_and_merge_api_refused(monkeypatch):
    # A sync-shaped PR cannot be landed by rebase, and --merge never admits a state PR.
    calls = probe(monkeypatch, payload())
    rebase = [w if w != "--merge" else "--rebase" for w in CMD]
    assert check(rebase)
    probe(monkeypatch, payload(baseRefName="state", headRefName="topic"))
    assert check()
    assert check(["gh", "api", "repos/example/protected/pulls/12/merge"])


def test_standalone_cli_refuses_non_sync_pr(tmp_path):
    # Real CLI process with a fake gh first on PATH: a green non-sync main PR is refused (exit 1).
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(payload(headRefName="topic")) + "\nEOF\n")
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "SHIP_ONLY_GUARD_REPOS": '["example/protected"]',
           "HOME": str(tmp_path)}
    script = str(Path(__file__).with_name("guard.py"))
    refused = subprocess.run([sys.executable, script, "--gh-args", *CMD[1:]], env=env,
                             capture_output=True, text=True, timeout=30)
    assert refused.returncode == 1 and "sync/upstream-" in refused.stderr
    fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(payload()) + "\nEOF\n")
    allowed = subprocess.run([sys.executable, script, "--gh-args", *GATE[1:]], env=env,
                             capture_output=True, text=True, timeout=30)
    assert allowed.returncode == 0, allowed.stderr

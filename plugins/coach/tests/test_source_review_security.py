"""Source-review B-CO1/B-CO2: subprocess readers must not execute input-supplied code."""
import importlib.util
import json
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location("coach_security_" + name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("target", [
    "-oProxyCommand=touch marker", "-Fconfig", "user@-oProxyCommand=x", "",
    "host name", "host\nname", "ssh://host", "host;id", "user@@host", None,
])
@pytest.mark.parametrize("entry", ["probe", "forward"])
def test_ssh_rejects_invalid_destinations_without_spawning(tmp_path, monkeypatch, target, entry):
    sg = load("signals")
    monkeypatch.setattr(sg, "collect", lambda *a: [])
    monkeypatch.setattr(sg, "reach_target", lambda *a: "user@node")
    monkeypatch.setattr(sg, "short_host", lambda: "node")
    calls = []
    runner = lambda *a: calls.append(a) or SimpleNamespace(returncode=0, stdout="", stderr="")
    if entry == "probe":
        ok, detail = sg.probe_host(target, {}, runner=runner)
    else:
        monkeypatch.setattr(sg, "save_cursor", lambda *a: pytest.fail("invalid destination advanced cursor"))
        result = sg.forward(tmp_path, "node", target, cfg={}, runner=runner)
        ok, detail = result["ok"], result["detail"]
    assert not calls, "untrusted destination reached ssh"
    assert not ok and "invalid ssh destination" in detail.lower()


@pytest.mark.parametrize("target", ["node", "node.example.com", "192.0.2.1", "user@node-1", "user.name@host_name"])
@pytest.mark.parametrize("entry", ["probe", "forward"])
def test_ssh_valid_destinations_are_after_option_terminator(tmp_path, monkeypatch, target, entry):
    sg = load("signals")
    monkeypatch.setattr(sg, "collect", lambda *a: [])
    monkeypatch.setattr(sg, "reach_target", lambda *a: "user@node")
    monkeypatch.setattr(sg, "short_host", lambda: "node")
    calls = []
    runner = lambda argv, *a: calls.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr="")
    if entry == "probe":
        assert sg.probe_host(target, {}, runner=runner)[0]
    else:
        assert sg.forward(tmp_path, "node", target, cfg={}, runner=runner)["ok"]
    assert calls[0][-3:] == ["--", target, "true" if entry == "probe" else sg.HUB_CMD]


def test_heartbeat_proxycommand_never_reaches_real_ssh(tmp_path, monkeypatch):
    sg = load("signals")
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "proxy-ran"
    reach = "-oProxyCommand=touch proxy-ran"
    # Ingested strings can survive in hosts.json; the execution boundary must reject them.
    sg.ingest(tmp_path, json.dumps({"kind": "heartbeat", "host": "node", "reach": reach}), now=1)
    stored = json.loads((tmp_path / "coach/signals/hosts.json").read_text())["node"]["reach"]
    # -F /dev/null avoids host configuration and no valid host is contacted.
    sg.ssh_argv = lambda cfg: ["ssh", "-F", "/dev/null", "-o", "ConnectTimeout=1"]
    awake, detail = sg.probe_host(stored, {})
    assert not marker.exists(), "heartbeat ProxyCommand executed locally"
    assert not awake and "invalid ssh destination" in detail.lower()


@pytest.mark.parametrize("driver", ["external", "textconv"])
def test_repo_diff_does_not_execute_repository_diff_drivers(tmp_path, monkeypatch, driver):
    so = load("second_opinion")
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout
    git("init", "-q")
    path = repo / "sample.txt"
    path.write_text("before\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "initial")
    marker = tmp_path / "driver-ran"
    script = tmp_path / "driver.sh"
    script.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nprintf 'driver output\\n'\n")
    script.chmod(0o700)
    if driver == "external":
        git("config", "diff.external", str(script))
        # Environment also enables the external-diff path unless explicitly disabled.
        monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(script))
    else:
        (repo / ".gitattributes").write_text("sample.txt diff=fixture\n")
        git("config", "diff.fixture.textconv", str(script))
    path.write_text("after\n")
    diff = so.repo_diff(str(repo), [str(path)])
    assert not marker.exists(), f"{driver} ran while gathering review evidence"
    assert "-before" in diff and "+after" in diff


def test_both_repo_diff_invocations_disable_drivers(monkeypatch):
    so = load("second_opinion")
    calls = []
    monkeypatch.setattr(so, "_git", lambda args, *a, **kw: calls.append(args) or "")
    so.repo_diff("/unused", [])
    diffs = [args for args in calls if args[0] == "diff"]
    assert len(diffs) == 2
    assert all("--no-ext-diff" in args and "--no-textconv" in args for args in diffs)

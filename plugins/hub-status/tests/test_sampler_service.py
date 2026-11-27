"""Real subprocesses, stubbed HERMES_HOME: no router imports or HTTP polling."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hub_status_installer_test", PLUGIN / "install_sampler.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def wait_for(predicate, timeout=29):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    pytest.fail("sampler did not observe the transition within 30 seconds")


def read_events(home):
    try:
        return [json.loads(line) for line in (home / "hub-status/events.jsonl").read_text().splitlines()]
    except FileNotFoundError:
        return []


def runtime(home, state, **kwargs):
    tmp = home / "gateway_state.tmp"
    tmp.write_text(json.dumps(dict(gateway_state=state, pid=os.getpid(), **kwargs)))
    tmp.replace(home / "gateway_state.json")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux systemd service")
def test_service_takes_over_after_unpolled_dashboard_restart(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    runtime(home, "running")
    # Stub systemd facts only; the actual classifier, log, flock, cadence and CLI run unchanged.
    bins = tmp_path / "bin"
    bins.mkdir()
    systemctl = bins / "systemctl"
    systemctl.write_text("#!/bin/sh\nprintf 'active\\n'\n")
    systemctl.chmod(0o755)
    env = dict(os.environ, HERMES_HOME=str(home), PYTHONPATH=str(PLUGIN),
               PATH=str(bins) + os.pathsep + os.environ.get("PATH", ""))
    # Model the old dashboard that had loaded its router before restarting. The replacement
    # dashboard never loads a router; there is deliberately no HTTP client in this test.
    dashboard = tmp_path / "dashboard.py"
    dashboard.write_text("import core, time\nassert core._SAMPLER.ensure()\ntime.sleep(120)\n")
    old = subprocess.Popen([sys.executable, str(dashboard)], env=env)
    service = None
    try:
        wait_for(lambda: read_events(home))
        service = subprocess.Popen([sys.executable, "-m", "core", "--home", str(home)], cwd=PLUGIN, env=env)
        # The service must stay running when the old dashboard owns the same lock.
        time.sleep(0.3)
        assert service.poll() is None
        old.terminate()
        old.wait(timeout=5)
        changed = time.monotonic()
        runtime(home, "stopped", restart_requested=True)
        wait_for(lambda: any(e["state"] == "restarting" for e in read_events(home)))
        assert time.monotonic() - changed < 30
        runtime(home, "running")
        wait_for(lambda: len(read_events(home)) >= 3)
        assert [e["state"] for e in read_events(home)] == ["up", "restarting", "up"]
    finally:
        for proc in (old, service):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="flock")
def test_exactly_one_sampler_per_home_across_processes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    runtime(home, "starting")
    probe = tmp_path / "claim.py"
    probe.write_text("import core, time\nprint(core._SAMPLER.ensure(), flush=True)\ntime.sleep(120)\n")
    env = dict(os.environ, HERMES_HOME=str(home), PYTHONPATH=str(PLUGIN))
    processes = [subprocess.Popen([sys.executable, str(probe)], env=env, stdout=subprocess.PIPE, text=True)
                 for _ in range(2)]
    try:
        claims = [p.stdout.readline().strip() for p in processes]
        assert sorted(claims) == ["False", "True"]
        winner = processes[claims.index("True")]
        loser = processes[claims.index("False")]
        winner.terminate()
        winner.wait(timeout=5)
        # Lock release is kernel-owned, not a stale lockfile deletion.
        third = subprocess.Popen([sys.executable, str(probe)], env=env, stdout=subprocess.PIPE, text=True)
        processes.append(third)
        assert third.stdout.readline().strip() == "True"
        assert loser.poll() is None
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()


def test_installer_scopes_home_and_has_no_gateway_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "argv", ["install_sampler.py", "--home", str(tmp_path / "profile home")])
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    installer.main()
    unit = (tmp_path / "config/systemd/user/hub-status-sampler.service").read_text()
    assert f'--home "{tmp_path / "profile home"}"' in unit
    assert "-m core" in unit and "Restart=always" in unit
    assert "gateway.service" not in unit and "dashboard.service" not in unit
    assert "After=default.target" not in unit  # would cycle with WantedBy
    assert [c[0][2:] for c in calls] == [["daemon-reload"], ["enable", "--now", "hub-status-sampler.service"],
                                       ["restart", "hub-status-sampler.service"]]
    assert all(c[1] == {"check": True} for c in calls)


def test_unit_path_escaping():
    assert installer._quote('/a%/$b/"c') == '"/a%%/$b/\\"c"'
    assert installer._quote('/a%/$b', command=True) == '"/a%%/$$b"'
    with pytest.raises(ValueError):
        installer._quote("bad\npath")


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("systemd-analyze"),
                    reason="requires systemd unit parser")
@pytest.mark.parametrize("plugin_name", ["plugin", 'plugin space$percent%quote"'])
def test_generated_service_is_accepted_by_real_systemd_parser(tmp_path, plugin_name):
    unit = tmp_path / "hub-status-sampler.service"
    # WorkingDirectory is a path directive, not an ExecStart-style quoted argument.
    plugin = tmp_path / plugin_name
    plugin.mkdir()
    unit.write_text(installer.service_text(tmp_path / "profile home", Path(sys.executable), plugin))
    result = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True,
                            timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr

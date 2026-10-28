"""Tests for chrome_recycle.py. Run: python3 -m pytest -q -p no:cacheprovider tests_chrome_recycle.py

Real child processes stand in for Chrome (they carry --user-data-dir=<tmp> in argv); systemctl is a recording fake.
"""

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("chrome_recycle", HERE / "chrome_recycle.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

SLEEPER = "import time\nwhile True: time.sleep(1)"
SPINNER = "while True: pass"


class Runner:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, "", "")


@pytest.fixture
def chrome(tmp_path):
    procs = []

    def start(code, *extra_arg):
        p = subprocess.Popen([sys.executable, "-c", code, f"--user-data-dir={tmp_path}/profile", *extra_arg])
        procs.append(p)
        time.sleep(0.3)
        return p

    yield start
    for p in procs:
        p.kill()
        p.wait()


def run(tmp_path, *flags, runner=None):
    runner = runner or Runner()
    rc = cr.main(["--home", str(tmp_path), "--user-data-dir", f"{tmp_path}/profile", "--sample-seconds", "1", *flags],
                 runner=runner)
    assert rc == 0
    return runner


def verdict(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_pids_match_user_data_dir_even_when_argv_is_one_space_joined_string(tmp_path, chrome):
    a = chrome(SLEEPER)
    b = subprocess.Popen([sys.executable, "-c", SLEEPER, f"x --user-data-dir={tmp_path}/profile y"])
    other = subprocess.Popen([sys.executable, "-c", SLEEPER, "--user-data-dir=/elsewhere"])
    try:
        time.sleep(0.3)
        pids = cr.chrome_pids(f"{tmp_path}/profile")
        assert a.pid in pids and b.pid in pids and other.pid not in pids
    finally:
        b.kill(), other.kill()


def test_below_threshold_does_not_restart(tmp_path, chrome, capsys):
    chrome(SLEEPER)
    r = run(tmp_path, "--threshold-mb", "999999", "--pressure-avail-mb", "0")
    assert verdict(capsys)["verdict"] == "below" and r.calls == []


def test_idle_over_threshold_restarts_and_records_cooldown(tmp_path, chrome, capsys):
    chrome(SLEEPER)
    r = run(tmp_path, "--threshold-mb", "0", "--pressure-avail-mb", "0")
    v = verdict(capsys)
    assert v["verdict"] == "recycle" and v["pss_mb"] > 0
    assert r.calls == [["systemctl", "--user", "restart", "hub-chrome.service"]]
    assert json.loads((tmp_path / "state" / "chrome-recycle.json").read_text())["last_recycle"] > 0
    r2 = run(tmp_path, "--threshold-mb", "0", "--pressure-avail-mb", "0")
    assert verdict(capsys)["verdict"] == "cooldown" and r2.calls == []


def test_busy_chrome_is_left_alone_without_pressure(tmp_path, chrome, capsys):
    chrome(SPINNER)
    r = run(tmp_path, "--threshold-mb", "0", "--pressure-avail-mb", "0", "--idle-cpu-pct", "25")
    v = verdict(capsys)
    assert v["verdict"] == "busy" and v["cpu_pct"] > 25 and r.calls == []


def test_pressure_recycles_even_a_busy_chrome(tmp_path, chrome, capsys):
    chrome(SPINNER)
    r = run(tmp_path, "--pressure-avail-mb", "1e12", "--pressure-threshold-mb", "0")
    v = verdict(capsys)
    assert v["verdict"] == "recycle" and v["pressure"] is True and len(r.calls) == 1


def test_hold_file_blocks_recycle(tmp_path, chrome, capsys):
    chrome(SLEEPER)
    (tmp_path / "chrome-recycle-hold").write_text("")
    r = run(tmp_path, "--pressure-avail-mb", "1e12", "--pressure-threshold-mb", "0")
    assert verdict(capsys)["verdict"] == "hold" and r.calls == []


def test_dry_run_and_failed_restart_do_not_record_cooldown(tmp_path, chrome, capsys):
    chrome(SLEEPER)
    r = run(tmp_path, "--threshold-mb", "0", "--pressure-avail-mb", "0", "--dry-run")
    assert verdict(capsys)["verdict"] == "recycle" and r.calls == []
    run(tmp_path, "--threshold-mb", "0", "--pressure-avail-mb", "0", runner=Runner(rc=1))
    assert verdict(capsys)["rc"] == 1
    assert not (tmp_path / "state" / "chrome-recycle.json").exists()

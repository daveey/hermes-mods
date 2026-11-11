"""Host-side runner lifecycle tests (run directly, not inside the fixture namespace).

A fake child stands in for the netns/pytest child so termination, timeout, cleanup
and receipt semantics are exercised without sudo. Foreign processes are only ever
spawned by these tests and are never signalled by the runner (asserted).
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import types
import uuid

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "scripts/run-fleet-scheduling"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def load_runner():
    loader = importlib.machinery.SourceFileLoader("run_fleet_scheduling_under_test", str(RUNNER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


FAKE_CHILD = textwrap.dedent('''
    import json, os, subprocess, sys, time
    from pathlib import Path
    manifest, mode = Path(sys.argv[1]), sys.argv[2]
    data = json.loads(manifest.read_text())
    root, out, name, core = Path(data["fixture_root"]), Path(data["output"]), data["scenario"], data["core_source"]
    env = dict(os.environ, FLEET_FIXTURE_ROOT=str(root), HERMES_HOME=str(root / "runtime"))
    if mode in ("hang", "hang-then-pass"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], env=env)
        Path(os.environ["FAKE_PIDFILE"]).write_text(str(child.pid))
        if mode == "hang":
            time.sleep(120)
    row = lambda result: [{"name": "t", "phase": "call", "result": result, "evidence": ""}]
    (out / f"{name}.tests.json").write_text(json.dumps(row("skipped" if mode == "skipped" else "passed")))
    (out / f"{name}.modules.json").write_text(json.dumps({"hermes_cli.kanban_db": core + "/hermes_cli/kanban_db.py"}))
    if mode != "no-preflight":
        (out / f"{name}-preflight.json").write_text(json.dumps({"scenario": name, "fixture_root": str(root)}))
    if name != "isolation" and mode != "no-proof":
        proof = {"fixture_root": str(root), "injected_fault": None, "transitions": [],
                 "assertions": [{"name": "a", "result": "PASS", "evidence": ""}]}
        if mode == "unverified":
            proof["result"] = "UNVERIFIED"
            proof["unverified"] = [{"reason": "cgroup_v2_unreadable"}]
        (out / f"{name}.proof.json").write_text(json.dumps(proof))
    sys.exit(0)
''')


@pytest.fixture
def harness(tmp_path, monkeypatch):
    runner = load_runner()
    core = tmp_path / "core"
    core.mkdir()
    (tmp_path / "fake_child.py").write_text(FAKE_CHILD)
    out = tmp_path / "out"
    out.mkdir()
    pidfile = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_PIDFILE", str(pidfile))
    monkeypatch.setattr(runner, "KILL_AFTER", 0)
    monkeypatch.setattr(runner, "OUTER_GRACE", 0)
    parent = tmp_path / "fixtures"
    parent.mkdir()
    box = types.SimpleNamespace(runner=runner, core=core, out=out, pidfile=pidfile, parent=parent, tmp=tmp_path)

    def factory(mode):
        return lambda options, manifest, root, timeout: [sys.executable, str(tmp_path / "fake_child.py"), str(manifest), mode]

    def run(mode, scenario="provider", timeout=3):
        options = types.SimpleNamespace(fixture_parent=parent, fixture_fault=None)
        return runner.run_scenario(scenario, options, core, None, out, {"core": "x"}, timeout, factory(mode))
    box.run, box.factory = run, factory
    return box


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def wait_dead(pid, seconds=5):
    deadline = time.monotonic() + seconds
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not alive(pid)


def receipt(box, name="provider"):
    return json.loads((box.out / f"{name}.json").read_text())


def test_clean_pass_and_isolation_needs_no_proof(harness):
    record, _ = harness.run("pass")
    assert record["result"] == "PASS" and record["cleanup"]["fixture_removed"] is True
    record, _ = harness.run("pass", scenario="isolation")
    assert record["result"] == "PASS", record["failures"]


def test_unverified_proof_survives_and_exit_nonzero(harness, monkeypatch):
    record, _ = harness.run("unverified")
    assert record["result"] == "UNVERIFIED" and not record["failures"]
    assert record["unverified"] == [{"reason": "cgroup_v2_unreadable"}]
    monkeypatch.setattr(harness.runner, "COMMAND_FACTORY", harness.factory("unverified"))
    monkeypatch.setattr(harness.runner, "revision", lambda path: "sha")
    rc = harness.runner.main(["--core-source", str(harness.core), "--output", str(harness.out), "--scenario", "provider",
                              "--fixture-parent", str(harness.parent)])
    assert rc == 1 and receipt(harness)["result"] == "UNVERIFIED"


@pytest.mark.parametrize("mode,needle", [("no-proof", "proof"), ("skipped", "non-passing/skipped"),
                                         ("no-preflight", "preflight")])
def test_missing_or_skipped_evidence_never_passes(harness, mode, needle):
    record, _ = harness.run(mode)
    assert record["result"] == "FAIL" and any(needle in item for item in record["failures"])


def test_timeout_kills_descendants_and_records_wait_bound(harness):
    record, _ = harness.run("hang", timeout=1)
    pid = int(harness.pidfile.read_text())
    assert wait_dead(pid)
    assert record["result"] == "FAIL" and "scenario timed out" in record["failures"]
    assert record["wait_bound"]["timed_out"] and record["wait_bound"]["scenario_timeout_s"] == 1
    assert record["cleanup"]["fixture_removed"] and any(item["pid"] == pid for item in record["cleanup"]["processes"]["signalled"])
    assert receipt(harness)["result"] == "FAIL"


def test_cleanup_failure_is_fail_and_preserves_root(harness, monkeypatch):
    def broken(root):
        raise OSError("injected cgroup cleanup failure")
    monkeypatch.setattr(harness.runner, "cleanup_cgroups", broken)
    record, _ = harness.run("pass")
    assert record["result"] == "FAIL" and any("cleanup failed" in item for item in record["failures"])
    root = Path(record["cleanup"]["root_preserved_for_diagnosis"])
    assert root.exists() and record["cleanup"]["fixture_removed"] is False
    shutil.rmtree(root)


def test_diagnostic_write_failure_never_succeeds(harness, monkeypatch, capsys):
    real = harness.runner.write_json

    def flaky(path, data):
        if path.name == "provider.json":
            raise OSError("disk full")
        return real(path, data)
    monkeypatch.setattr(harness.runner, "write_json", flaky)
    record, _ = harness.run("pass")
    assert record["result"] == "FAIL" and any("receipt write failed" in item for item in record["failures"])
    assert json.loads((harness.out / "provider.receipt-fallback.json").read_text())["result"] == "FAIL"
    monkeypatch.setattr(harness.runner, "write_json", lambda path, data: (_ for _ in ()).throw(OSError("dead")))
    record, _ = harness.run("pass")
    assert record["result"] == "FAIL" and '"result": "FAIL"' in capsys.readouterr().err


def test_source_drift_is_rejected(harness, monkeypatch):
    real, calls = harness.runner.sha_map, []

    def drifting(extra=()):
        calls.append(1)
        value = real(extra)
        return value if len(calls) == 1 else {**value, "tests/fleet_scheduling/test_scenarios.py": "drifted"}
    monkeypatch.setattr(harness.runner, "sha_map", drifting)
    record, _ = harness.run("pass")
    assert record["result"] == "FAIL" and any("source changed" in item for item in record["failures"])


def test_launcher_sigterm_writes_fail_receipt_and_reaps_descendants(harness, tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f'''
        import sys
        sys.path.insert(0, {str(RUNNER.parent)!r}); sys.path.insert(0, {str(Path(__file__).parent)!r})
        import test_runner
        runner = test_runner.load_runner()
        runner.COMMAND_FACTORY = lambda options, manifest, root, timeout: [sys.executable, {str(tmp_path / "fake_child.py")!r}, str(manifest), "hang"]
        runner.KILL_AFTER = 0
        sys.exit(runner.main(sys.argv[1:]))
    '''))
    env = dict(os.environ, FAKE_PIDFILE=str(harness.pidfile), FLEET_RUNNER_TEST_TIMEOUT="1")
    proc = subprocess.Popen([sys.executable, str(driver), "--core-source", str(RUNNER.parent.parent), "--output", str(harness.out),
                             "--scenario", "all", "--timeout", "3", "--fixture-parent", str(harness.parent)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 30
    while not harness.pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert harness.pidfile.exists()
    pid = int(harness.pidfile.read_text())
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 143, err
    assert wait_dead(pid)
    data = receipt(harness)
    assert data["result"] == "FAIL" and "launcher terminated by signal" in data["failures"]
    assert data["wait_bound"]["terminated"] and data["cleanup"]["fixture_removed"]
    assert receipt(harness, "wake")["failures"] == ["not run: launcher terminated"]
    assert not list(harness.parent.iterdir())


# ----------------------------- cgroup registry -----------------------------

def fx_process(root):
    env = {"PATH": os.defpath, "FLEET_FIXTURE_ROOT": str(root), "HERMES_HOME": str(root / "runtime")}
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env=env)


def test_cgroup_registry_validation_negative_controls_and_nested_discovery(tmp_path, monkeypatch):
    runner = load_runner()
    cg = tmp_path / "cgroup"
    (cg / "user").mkdir(parents=True)
    monkeypatch.setattr(runner, "CGROUP_ROOT", cg)
    root = tmp_path / "fx"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    owned = cg / "user" / ("fleet-oom-" + uuid.uuid4().hex)
    owned.mkdir()
    victim = cg / "user" / ("fleet-oom-" + uuid.uuid4().hex)  # valid name but only reachable via symlink
    victim.mkdir()
    link = cg / "user" / ("fleet-oom-" + uuid.uuid4().hex)
    link.symlink_to(victim, target_is_directory=True)
    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    bad = ["relative/fleet-oom-" + "a" * 32, str(cg / "user" / "other-group"), str(cg / "user" / ".." / "x"),
           str(foreign_dir), str(cg / ("fleet-oom-" + "b" * 32)), str(link), "\0", 7,
           str(cg / "user" / ("fleet-oom-" + "A" * 32))]
    (nested / "fleet-cgroups.json").write_text(json.dumps({"version": 1, "groups": [str(owned), *bad]}))
    result = runner.cleanup_cgroups(root)
    states = {entry["group"]: entry["state"] for entry in result["groups"]}
    assert states[str(owned)] == "removed" and not owned.exists()
    assert all(states[item] == "rejected" for item in bad)
    assert victim.exists() and foreign_dir.exists() and len(result["errors"]) == len(bad)
    (root / "fleet-cgroups.json").write_text(json.dumps({"version": 2, "groups": []}))
    assert any("unreadable registry" in item for item in runner.cleanup_cgroups(root)["errors"])


def test_cgroup_reap_kills_only_identity_registered_fixture_pids(tmp_path, monkeypatch):
    runner = load_runner()
    cg = tmp_path / "cgroup"
    (cg / "user").mkdir(parents=True)
    monkeypatch.setattr(runner, "CGROUP_ROOT", cg)
    root = tmp_path / "fx"
    (root / "runtime").mkdir(parents=True)
    group = cg / "user" / ("fleet-oom-" + uuid.uuid4().hex)
    group.mkdir()
    (root / "fleet-cgroups.json").write_text(json.dumps({"version": 1, "groups": [str(group)]}))
    mine, foreign = fx_process(root), subprocess.Popen(["sleep", "60"], env={"PATH": os.defpath})
    try:
        time.sleep(0.3)
        state = {"pids": [mine.pid, foreign.pid]}
        monkeypatch.setattr(runner, "read_procs", lambda g: [p for p in state["pids"] if alive(p) and (p != mine.pid or mine.poll() is None)])
        result = runner.cleanup_cgroups(root)
        assert mine.wait(timeout=5) == -signal.SIGKILL
        assert foreign.poll() is None, "foreign pid in a published group must never be signalled"
        assert result["groups"][0]["state"] == "failed" and "not signalled" in result["errors"][0]
    finally:
        for proc in (mine, foreign):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


def test_process_cleanup_refuses_lost_marker_and_spares_foreign(tmp_path):
    runner = load_runner()
    root = tmp_path / "fx"
    (root / "runtime").mkdir(parents=True)
    other = tmp_path / "other"
    env = {"PATH": os.defpath, "HERMES_HOME": str(root / "runtime"), "FLEET_FIXTURE_ROOT": str(other)}
    lost = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env=env)
    try:
        time.sleep(0.3)
        result = runner.cleanup_processes(root)
        assert lost.poll() is None and any("lost ownership marker" in item for item in result["errors"])
        assert result["signalled"] == []
    finally:
        lost.kill()
        lost.wait(timeout=5)


# ---------------------- end-to-end unavailable-OOM fault ----------------------

def test_e2e_unavailable_oom_is_unverified_not_pass(tmp_path):
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        pytest.skip("passwordless sudo for the unshare launcher unavailable")
    out = tmp_path / "evidence"
    core = os.environ.get("FLEET_CORE_SOURCE")
    assert core, "set FLEET_CORE_SOURCE to the explicit core checkout for this integration test"
    proc = subprocess.run([sys.executable, str(RUNNER), "--core-source", core, "--output", str(out), "--scenario", "crash",
                           "--timeout", "240", "--fixture-fault", "hide-cgroupfs"], capture_output=True, text=True, timeout=330)
    data = json.loads((out / "crash.json").read_text())
    assert proc.returncode != 0 and data["result"] == "UNVERIFIED", (proc.stdout, proc.stderr, data.get("failures"))
    assert data["runner_fault"] == "hide-cgroupfs" and not data["failures"]
    assert "cgroup_v2_unreadable" in json.dumps(data["unverified"]), data["unverified"]
    assert data["cleanup"]["fixture_removed"] and data["cleanup"]["fixture_processes_remaining"] == []

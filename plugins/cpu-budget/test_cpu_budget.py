"""Tests for cpu_budget. Run: python3 -m pytest -q -p no:cacheprovider plugins/cpu-budget/test_cpu_budget.py

Everything runs on an injected clock, a synthetic ps table, fake probes and a fake Kanban runner with a
tmp state dir. An autouse guard fails any subprocess other than /bin/ps, so no test can create a real card.
"""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("cpu_budget", Path(__file__).with_name("cpu_budget.py"))
cb = importlib.util.module_from_spec(_spec)
sys.modules["cpu_budget"] = cb
_spec.loader.exec_module(cb)

T0 = 1_800_000_000.0
SERVICES = [
    {"name": "rpc", "match": r"rpc_worker\.py"},
    {"name": "listening", "match": r"listening_service\.py"},
    {"name": "pod", "match": r"/BBPod(\s|$)"},
    {"name": "hub", "match": r"/BBHub(\s|$)"},
    {"name": "iroh", "match": r"iroh_service\.py"},
]
ARGS = {"rpc": "python3 rpc_worker.py", "listening": "python3 listening_service.py", "pod": "/x/BBPod",
        "hub": "/x/BBHub --menu-bar", "iroh": "python3 iroh_service.py"}
PIDS = {"rpc": 101, "listening": 102, "pod": 103, "hub": 104, "iroh": 105}


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch):
    real = subprocess.run

    def guard(argv, *a, **k):
        if argv and argv[0] == "/bin/ps":
            return real(argv, *a, **k)
        raise AssertionError("test attempted a real subprocess: %r" % (argv,))

    monkeypatch.setattr(subprocess, "run", guard)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Popen")))


class Sim:
    """Synthetic host: advances a clock and per-service cumulative CPU."""

    def __init__(self, tmp_path, status=None, sink_runner=None, **cfg):
        self.now = T0
        self.started = {s: T0 - 86400 for s in ARGS}
        self.cpu = {s: 1000.0 for s in ARGS}
        self.pids = dict(PIDS)
        self.absent = set()
        self.ps_ok = True
        self.status = status if status is not None else {"conversation": "idle", "ci": "idle"}
        self.runner = sink_runner or FakeKanban()
        sink = cb.KanbanSink(["hermes"], "bb", "bb-queue", runner=self.runner, host="mini")
        config = {"host": "mini", "services": SERVICES, "probes": [
            {"type": "fake", "kind": "conversation", "name": "c"}, {"type": "fake", "kind": "ci", "name": "ci"}]}
        config.update(cfg)
        self.det = cb.Detector(config, tmp_path, sink, ps_fn=self.ps, clock=lambda: self.now,
                               probe_registry={"fake": lambda spec, rows, now, since: (self.status[spec["kind"]], "fake")},
                               self_pid=1)
        self.results = []

    def ps(self):
        if not self.ps_ok:
            return None
        lines = []
        for s, args in ARGS.items():
            if s in self.absent:
                continue
            lstart = time.strftime("%a %b %e %H:%M:%S %Y", time.localtime(self.started[s]))
            c = self.cpu[s]
            lines.append("%5d %s %d:%05.2f %s" % (self.pids[s], lstart, int(c // 60), c % 60, args))
        lines.append("    1 %s 0:01.00 /sbin/launchd" % time.strftime("%a %b %e %H:%M:%S %Y", time.localtime(T0 - 9e5)))
        return "\n".join(lines) + "\n"

    def step(self, dt=30, pct=0.0):
        """Advance dt seconds burning ``pct`` percent of one core in total (spread evenly) then tick."""
        self.now += dt
        for s in self.cpu:
            if s not in self.absent:
                self.cpu[s] += dt * pct / 100.0 / len(self.cpu)
        r = self.det.tick()
        self.results.append(r)
        return r

    def run(self, seconds, pct, dt=30):
        out = []
        for _ in range(int(seconds // dt)):
            out.append(self.step(dt, pct))
        return out

    def windows(self):
        return [r for r in self.results if "window" in r]


class FakeKanban:
    def __init__(self):
        self.calls = []
        self.tasks = {}
        self.fail = False

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        verb = argv[argv.index("--board") + 2]
        if self.fail:
            return 1, ""
        if verb == "create":
            key = argv[argv.index("--idempotency-key") + 1]
            for tid, t in self.tasks.items():
                if t["key"] == key:
                    return 0, json.dumps({"id": tid})
            tid = "t_%d" % (len(self.tasks) + 1)
            self.tasks[tid] = {"key": key, "status": "ready", "comments": [], "argv": argv, "body": stdin}
            return 0, json.dumps({"id": tid})
        if verb == "show":
            return 0, json.dumps({"task": {"status": self.tasks[argv[-1]]["status"]}})
        if verb == "comment":
            self.tasks[argv[-2]]["comments"].append(argv[-1])
            return 0, ""
        return 1, ""


def start(sim):
    assert sim.step(30)["action"] == "baseline"
    sim.status_prev_primed = True


# ------------------------------------------------------------------ accounting
def test_cputime_and_ps_parsing():
    assert cb.parse_cputime("1:02.50") == pytest.approx(62.5)
    assert cb.parse_cputime("1:01:01") == 3661
    assert cb.parse_cputime("2-00:00:10") == 172810
    rows = cb.parse_ps("  42 Thu Oct  1 03:04:05 2026 1:00.25 /x/BBHub --menu-bar\n")
    assert rows[0]["pid"] == 42 and rows[0]["cpu_s"] == 60.25 and rows[0]["args"] == "/x/BBHub --menu-bar"


def test_normal_idle_below_budget_never_reports(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(3600, 10)  # 10% of a core, idle evidence everywhere
    ws = sim.windows()
    assert len(ws) == 6 and all(w["window"] == "idle_ok" for w in ws)
    assert all(w["pct"] == pytest.approx(10.0, abs=0.5) for w in ws)
    assert sim.runner.calls == []


def test_overbudget_two_windows_opens_one_agent_owned_card(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(600, 120)
    assert sim.windows()[-1]["window"] == "idle_over" and sim.runner.calls == []  # one window is not enough
    sim.run(600, 120)
    assert sim.windows()[-1]["incident"] == "created"
    (tid, task), = sim.runner.tasks.items()
    argv = task["argv"]
    assert argv[argv.index("--assignee") + 1] == "bb-queue" and "--json" in argv
    assert "needs" not in " ".join(argv).lower().replace("--no-requires", "")
    assert not any("inbox" in c[0].__repr__().lower() or "human" in c[0].__repr__().lower() for c in sim.runner.calls)
    assert "120" in task["body"] and "rpc" in task["body"]


def test_exactly_at_budget_is_not_over(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(1200, 40)
    assert [w["window"] for w in sim.windows()] == ["idle_ok", "idle_ok"] and not sim.runner.calls


def test_normal_window_between_resets_streak(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(600, 120)
    sim.run(600, 5)
    sim.run(600, 120)
    assert [w["window"] for w in sim.windows()] == ["idle_over", "idle_ok", "idle_over"] and not sim.runner.calls


def test_active_or_unknown_evidence_never_counts_as_idle(tmp_path):
    for status, expected in (({"conversation": "active", "ci": "idle"}, "active"),
                             ({"conversation": "idle", "ci": "unknown"}, "incomplete"),
                             ({"conversation": "idle", "ci": "active"}, "active")):
        sim = Sim(tmp_path / expected / str(status["ci"]), status=status)
        start(sim)
        sim.run(1800, 200)
        assert {w["window"] for w in sim.windows()} == {expected}
        assert sim.runner.calls == []


def test_missing_evidence_kind_is_unknown_not_idle(tmp_path):
    sim = Sim(tmp_path)
    sim.det.probes = [{"type": "fake", "kind": "ci", "name": "ci"}]  # no conversation probe at all
    start(sim)
    sim.run(1800, 200)
    assert {w["window"] for w in sim.windows()} == {"incomplete"} and not sim.runner.calls


def test_probe_exception_is_unknown(tmp_path):
    status, detail = cb.evaluate_idle([{"type": "boom", "kind": "ci"}], [], T0, T0 - 30,
                                      {"boom": lambda *a: 1 / 0})
    assert status == "unknown" and "probe error" in detail[0]


def test_activity_in_one_interval_spoils_only_that_window(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(300, 120)
    sim.status["ci"] = "active"
    sim.step(30, 120)
    sim.status["ci"] = "idle"
    sim.run(270, 120)
    sim.run(600, 120)
    assert [w["window"] for w in sim.windows()] == ["active", "idle_over"] and not sim.runner.calls


def test_pid_reuse_is_new_identity_not_negative_delta(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(270, 120)
    # rpc exits and an unrelated process reuses pid 101 (new start time, tiny cputime)
    sim.started["rpc"] = sim.now + 5
    sim.cpu["rpc"] = 2.0
    sim.step(30, 0)
    w = sim.run(300, 120)[-1]
    assert w["window"] == "incomplete"  # restart observed: window cannot be called complete
    assert "restart:rpc" in json.loads(next((tmp_path).glob("cpu-budget-windows-*.jsonl")).read_text().splitlines()[0])["bad"]
    # accounting never went negative
    assert w["pct"] >= 0


def test_restart_counts_new_process_cpu_but_flags_window(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    prev = cb._load_snap(sim.det._load()["snap"])
    sim.now += 30
    sim.started["hub"] = sim.now - 10
    sim.cpu["hub"] = 7.0
    cur = cb.snapshot(cb.parse_ps(sim.ps()), SERVICES, 1)
    acc = cb.account_interval(prev, cur, SERVICES, sim.now - 30, sim.now, 90)
    assert acc["cpu"]["hub"] == pytest.approx(7.0)
    assert "restart:hub" in acc["reasons"] and "exited:hub" in acc["reasons"]


def test_unbaselined_preexisting_process_is_baseline_only(tmp_path):
    prev = {s["name"]: {} for s in SERVICES}
    cur = {s["name"]: {} for s in SERVICES}
    cur["rpc"] = {(5, T0 - 1000): 500.0}
    acc = cb.account_interval(prev, cur, [SERVICES[0]], T0, T0 + 30, 90)
    assert acc["cpu"]["rpc"] == 0.0 and acc["reasons"] == ["unbaselined:rpc"]


def test_missing_required_process_makes_window_incomplete(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(270, 120)
    sim.absent.add("iroh")
    sim.run(30, 120)
    sim.run(600, 120)
    sim.absent.clear()
    sim.started["iroh"] = sim.now + 1
    assert all(w["window"] == "incomplete" for w in sim.windows()) and not sim.runner.calls


def test_optional_service_may_be_absent(tmp_path):
    services = [dict(s, required=(s["name"] != "iroh")) for s in SERVICES]
    sim = Sim(tmp_path, services=services)
    sim.absent.add("iroh")
    start(sim)
    sim.run(1200, 120)
    assert [w["window"] for w in sim.windows()] == ["idle_over", "idle_over"]
    assert sim.windows()[-1]["incident"] == "created"


def test_incomplete_sample_gap_ps_failure_and_too_soon(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(270, 120)
    sim.ps_ok = False
    assert sim.step(30, 120)["action"] == "ps_failed"
    sim.ps_ok = True
    sim.step(120, 120)  # 150 s since the last good sample: gap > 90 s
    sim.run(180, 120)
    assert sim.windows()[-1]["window"] == "incomplete"
    # samples faster than 30 s are ignored, not accumulated
    sim.step(30, 120)
    before = sim.det._load()["window"]["wall_s"]
    assert sim.step(5, 120)["action"] == "skipped_too_soon"
    assert sim.det._load()["window"]["wall_s"] == before


def test_corrupt_state_restarts_from_baseline(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    (tmp_path / "cpu-budget-state.json").write_text("{not json")
    assert sim.step(30, 120)["action"] == "baseline"


def test_cpu_going_backwards_is_incomplete(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.cpu["pod"] = 10.0
    sim.step(30, 0)
    assert "cpu_went_back:pod" in sim.det._load()["window"]["bad"]


# ------------------------------------------------------------------ incident dedup
def test_incident_dedup_repeat_appends_comment_and_never_creates_second(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(1200, 120)
    assert len(sim.runner.tasks) == 1
    sim.run(600, 120)  # third over window inside repeat_min_s: throttled, no spam
    assert sim.windows()[-1]["incident"] == "repeat_throttled"
    sim.run(1800, 120)  # past repeat_min_s
    assert any(w.get("incident") == "commented" for w in sim.windows())
    (tid, task), = sim.runner.tasks.items()
    assert len(sim.runner.tasks) == 1 and task["comments"] and task["comments"][0].startswith("repeat: cpu-budget")
    # idempotency key is stable per episode
    assert task["key"] == "cpu-budget:mini:1"


def test_closed_incident_starts_new_episode_key(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(1200, 120)
    sim.runner.tasks["t_1"]["status"] = "done"
    sim.run(2400, 120)
    assert sorted(t["key"] for t in sim.runner.tasks.values()) == ["cpu-budget:mini:1", "cpu-budget:mini:2"]


def test_sink_failure_does_not_lose_state_and_retries(tmp_path):
    sim = Sim(tmp_path)
    sim.runner.fail = True
    start(sim)
    sim.run(1200, 120)
    assert sim.windows()[-1]["incident"] == "create_failed" and not sim.runner.tasks
    sim.runner.fail = False
    sim.run(600, 120)
    assert sim.windows()[-1]["incident"] == "created" and len(sim.runner.tasks) == 1


def test_sink_argv_targets_board_and_uses_stdin_body(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(1200, 120)
    argv, stdin = sim.runner.calls[0]
    assert argv[:4] == ["hermes", "kanban", "--board", "bb"] and "--body-file" in argv and stdin
    assert "--idempotency-key" in argv


# ------------------------------------------------------------------ probes against a real filesystem
def test_files_quiet_probe(tmp_path):
    d = tmp_path / "turns"
    spec = {"globs": [str(d / "*.json")], "quiet_s": 60}
    assert cb.probe_files_quiet(spec, None, T0, T0 - 30)[0] == "unknown"  # missing dir
    d.mkdir()
    import os
    os.utime(d, (T0 - 9999, T0 - 9999))
    assert cb.probe_files_quiet(spec, None, T0, T0 - 30)[0] == "idle"
    f = d / "a.json"
    f.write_text("{}")
    os.utime(f, (T0 - 10, T0 - 10))
    assert cb.probe_files_quiet(spec, None, T0, T0 - 30)[0] == "active"


def test_process_absent_probe():
    rows = [{"pid": 7, "start": 0, "cpu_s": 0, "args": "/runner/Runner.Worker run"}]
    assert cb.probe_process_absent({"patterns": ["Runner\\.Worker"]}, rows, T0, T0)[0] == "active"
    assert cb.probe_process_absent({"patterns": ["xcodebuild"]}, rows, T0, T0)[0] == "idle"
    assert cb.probe_process_absent({"patterns": ["x"]}, None, T0, T0)[0] == "unknown"


# ------------------------------------------------------------------ no production side effects + failure injection
def test_default_sink_is_off_without_config_and_state_stays_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = {"services": SERVICES, "probes": []}
    assert cb.build_sink(cfg) is None
    d = cb.Detector(cfg, tmp_path / "state", None, ps_fn=lambda: None, clock=lambda: T0)
    assert d.tick()["action"] == "ps_failed"
    assert not (tmp_path / "home").exists()


def test_config_roundtrip_and_sink_resolution(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"services": SERVICES, "sink": {"board": "bb", "assignee": "bb-queue",
                                                              "hermes_candidates": [sys.executable]}}))
    cfg = cb.load_config(str(p))
    sink = cb.build_sink(cfg)
    assert sink.hermes == [sys.executable] and sink.board == "bb"
    with pytest.raises(ValueError):
        p.write_text("{}")
        cb.load_config(str(p))


def test_failure_injection_spinner_script(tmp_path, monkeypatch):
    import os
    marker = "fixture_svc_%d" % time.time_ns()
    monkeypatch.undo()  # drop the autouse guard for this isolated fixture only
    spinner = subprocess.Popen([sys.executable, "-c", "while True: pass  # " + marker, marker])
    try:
        services = [{"name": "spin", "match": marker}]
        time.sleep(1.0)
        a = cb.snapshot(cb.parse_ps(cb.run_ps()), services, os.getpid())
        time.sleep(4.0)
        b = cb.snapshot(cb.parse_ps(cb.run_ps()), services, os.getpid())
    finally:
        spinner.kill()
        spinner.wait()
    (ident, c1), = a["spin"].items()
    c2 = b["spin"][ident]
    assert c2 - c1 >= 2.5  # ~4 s of cpu, cputime granularity on Linux is 1 s
    acc = cb.account_interval(a, b, services, T0, T0 + 30, 90)
    assert acc["reasons"] == [] and acc["cpu"]["spin"] == pytest.approx(c2 - c1)


# ------------------------------------------------------------------ review findings (clock, point-in-time probes, empty specs)
def test_clock_stepping_back_rebaselines_and_never_inflates_cpu(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(300, 30)
    sim.now -= 230
    sim.cpu = {k: v + 9.0 for k, v in sim.cpu.items()}
    assert sim.det.tick()["action"] == "clock_back"
    sim.run(1200, 30)
    assert {w["window"] for w in sim.windows()} == {"idle_ok"} and not sim.runner.calls  # true load is 30%


def test_point_in_time_probe_active_at_previous_sample_taints_next_interval(tmp_path):
    sim = Sim(tmp_path)
    start(sim)
    sim.run(270, 120)
    sim.status["ci"] = "active"
    sim.step(30, 120)  # a CI job is seen at this sample...
    sim.status["ci"] = "idle"
    sim.step(30, 120)  # ...and gone at the next: the interval in between had CI activity
    assert "idle:active" in sim.det._load()["window"]["bad"]
    # and the very first interval after a baseline has no previous reading, so it is unknown
    st = {}
    assert cb.evaluate_idle(sim.det.probes, [], T0, T0 - 30, {"fake": lambda s, r, n, sc: ("idle", "x")},
                            prev=st)[0] == "unknown"


def test_empty_specs_and_unparseable_ps_are_not_evidence(tmp_path):
    assert cb.probe_files_quiet({"globs": []}, None, T0, T0 - 30)[0] == "unknown"
    assert cb.probe_process_absent({"patterns": []}, [], T0, T0)[0] == "unknown"
    assert cb.evaluate_idle([{"type": "files_quiet", "kind": "conversation", "globs": []},
                             {"type": "process_absent", "kind": "ci", "patterns": []}], [], T0, T0)[0] == "unknown"
    det = cb.Detector({"services": SERVICES, "probes": []}, tmp_path, None, ps_fn=lambda: "garbage line\n",
                      clock=lambda: T0)
    assert det.tick()["action"] == "ps_failed"
    assert cb.evaluate_idle([{"kind": "ci"}], [], T0, T0)[0] == "unknown"  # spec without a type
    assert cb.evaluate_idle([{"type": "x", "kind": "ci"}], [], T0, T0, registry={})[0] == "unknown"


def test_self_exclusion_does_not_hide_builds_named_like_the_detector():
    rows = [{"pid": 9, "start": 0, "cpu_s": 0, "args": "pytest plugins/cpu-budget/test_cpu_budget.py"}]
    assert cb.probe_process_absent({"patterns": ["pytest"]}, rows, T0, T0)[0] == "active"
    own = [{"pid": 9, "start": 0, "cpu_s": 0, "args": "/usr/bin/python3 /x/mac-load-recorder sample --threshold 20"}]
    assert cb.probe_process_absent({"patterns": ["python3"]}, own, T0, T0)[0] == "idle"


def test_ps_is_called_wide_so_launchd_does_not_truncate_args(monkeypatch):
    seen = {}

    def fake(argv, *a, **k):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "1 Thu Jan  1 00:00:00 1970 0:00.00 x\n", "")

    monkeypatch.setattr(subprocess, "run", fake)
    cb.run_ps()
    assert "-Aww" in seen["argv"]

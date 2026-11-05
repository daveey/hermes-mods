"""gateway-9aaf151a15 (came back): a backend stop carries its cause, so different causes get different IDs.

Core's crash note ("Your previous turn was interrupted mid-run — the app or its backend process stopped") said nothing
about WHY the backend stopped, so every stop on any host shared one fingerprint: once one cause was fixed, the next stop
from any other cause looked like a failed fix. self_heal.stop_cause reads, on the host that ran the turn, the evidence
in the window between the cut turn's start and the resume (update receipts, hermes-sync `mods-reload:` lines, gateway
exit diagnostics, macOS sleep), and the symptom/fingerprint carry it."""
import datetime as _dt
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


sh = load("coach_self_heal_stop_test", ROOT / "self_heal.py")
core = load("coach_core_stop_test", ROOT / "core.py")
plugin = load("coach_plugin_stop_test", ROOT / "__init__.py")


def judge(p=0.0):
    calls = []

    def llm(system, prompt):
        calls.append((system, prompt))
        return json.dumps({"p_defect": p, "kind": "complaint", "component": "desktop", "symptom": "x"})
    return llm, calls


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)


def events(tmp_path, name="selfheal_detect"):
    p = tmp_path / "coach" / "log.jsonl"
    if not p.exists():
        return []
    return [r for r in (json.loads(x) for x in p.read_text().splitlines()) if r.get("event") == name]


CRASH_NOTE = ("[System note: Your previous turn was interrupted mid-run — the app or its backend process stopped "
              "before the turn could finish. The interrupted request was:]\n\nfix the panel")
T0 = 1_800_000_000.0  # the cut turn starts here; the crash note arrives 30 min later


def _local(ts):
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


def _receipt(home, ts, name="update_x.json"):
    d = home / "logs" / "update_receipts"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"schema": 1, "started_at": _iso(ts), "finished_at": _iso(ts + 120),
                                      "outcome": "success", "pre_update": {"short_sha": "af81aae2"},
                                      "post_update": {"short_sha": "19c28915"}}))


def _sync_line(home, ts, msg="mods-reload: gateway 4242 in /x; trigger plugins/coach/self_heal.py"):
    (home / "logs").mkdir(parents=True, exist_ok=True)
    with open(home / "logs" / "hermes-sync.log", "a") as fh:
        fh.write(f"{_local(ts)} [host] mods: fast-forward\n{_local(ts)} [host] {msg}\n")


def _exit_diag(home, ts, tag="asyncio.run.SystemExit"):
    (home / "logs").mkdir(parents=True, exist_ok=True)
    with open(home / "logs" / "gateway-exit-diag.log", "a") as fh:
        fh.write(json.dumps({"ts": _iso(ts), "tag": tag, "pid": 77, "code": 75}) + "\n")


def _pmset_lines(ts):
    stamp = _dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    return [f"{stamp} Wake                \tWake from Normal Sleep [CDNVA] : due to UserActivity",
            f"{stamp} Sleep               \tEntering Sleep state due to 'Idle Sleep':TCPKeepAlive=active Using BATT"]


def _crash_through_hooks(tmp_path, monkeypatch, setup=None, pmset=None):
    """The incident path: a turn starts, the backend stops, 30 min later core's crash note arrives (pre_llm_call)."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(sh, "_pmset_log", lambda: list(pmset or []), raising=False)
    if setup:
        setup(home)
    now = [T0]
    store = core.Store(tmp_path / "coach", clock=lambda: now[0])
    llm, _ = judge(0.0)
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    live = plugin._load("self_heal")  # the module copy the hooks actually call
    monkeypatch.setattr(live, "_pmset_log", lambda: list(pmset or []), raising=False)
    hooks["pre_llm_call"](session_id="s1", user_message="fix the panel")
    now[0] = T0 + 1800
    hooks["pre_llm_call"](session_id="s1", user_message=CRASH_NOTE)
    ev = events(tmp_path)
    assert len(ev) == 1, ev
    return ev[0]


def test_crash_note_after_core_update_gets_its_own_cause_and_id(tmp_path, on, monkeypatch):
    ev = _crash_through_hooks(tmp_path, monkeypatch, lambda h: _receipt(h, T0 + 600))
    assert ev["symptom"] == "turn interrupted (backend stopped: core update)"
    assert ev["fingerprint"] != "gateway-9aaf151a15"
    assert ev["stop_cause"] == "core update" and "19c28915" in ev["stop_evidence"]


def test_crash_note_after_mods_reload_is_a_plugin_reload(tmp_path, on, monkeypatch):
    ev = _crash_through_hooks(tmp_path, monkeypatch, lambda h: _sync_line(h, T0 + 900))
    assert ev["symptom"] == "turn interrupted (backend stopped: plugin reload)"
    assert ev["fingerprint"] != "gateway-9aaf151a15"
    assert "mods-reload: gateway 4242" in ev["stop_evidence"] and "fast-forward" not in ev["stop_evidence"]


def test_crash_note_after_mac_sleep_is_machine_slept(tmp_path, on, monkeypatch):
    ev = _crash_through_hooks(tmp_path, monkeypatch, pmset=_pmset_lines(T0 + 300))
    assert ev["symptom"] == "turn interrupted (backend stopped: machine slept)"
    assert "Entering Sleep state" in ev["stop_evidence"]


def test_crash_note_after_gateway_exit_is_gateway_shutdown(tmp_path, on, monkeypatch):
    ev = _crash_through_hooks(tmp_path, monkeypatch, lambda h: _exit_diag(h, T0 + 1200))
    assert ev["symptom"] == "turn interrupted (backend stopped: gateway shutdown)"
    assert "asyncio.run.SystemExit" in ev["stop_evidence"]


def test_crash_note_without_evidence_in_the_window_keeps_the_old_id(tmp_path, on, monkeypatch):
    # evidence outside the window (before the cut turn started, after the resume) is not this stop's cause
    def setup(h):
        _receipt(h, T0 - 7200)
        _sync_line(h, T0 + 3600)
        _exit_diag(h, T0 - 4000)
    ev = _crash_through_hooks(tmp_path, monkeypatch, setup, pmset=_pmset_lines(T0 - 9000))
    assert ev["symptom"] == "turn interrupted (backend stopped)"
    assert ev["fingerprint"] == "gateway-9aaf151a15"
    assert ev["stop_cause"] == "unknown"


def test_stop_evidence_is_capped_and_names_the_host(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_pmset_log", lambda: [])
    home = tmp_path / "home"
    for i in range(40):
        _sync_line(home, T0 + 10 + i, msg=f"mods-reload: backend desktop-serve:9147 ok (coach) number {i} " + "x" * 40)
    got = sh.stop_cause(home, T0, T0 + 1800)
    assert got["cause"] == "plugin reload" and 0 < len(got["evidence"]) <= 600
    assert got["host"] == sh.host_name()


def test_guard_every_cause_has_a_distinct_id_and_every_crash_alert_logs_a_cause(tmp_path, on, monkeypatch):
    """Detection guard: two causes must never share an ID (else a fixed cause masks the next), and a crash-note
    alert is never logged without a stop_cause field, whatever caller produced the hit."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sh, "_pmset_log", lambda: [])
    fps = {}
    for cause in sh.STOP_CAUSES + ("unknown",):
        hit = sh.resume_signal(CRASH_NOTE, 1800, stop={"cause": cause, "evidence": "x", "host": "h"})
        fps[cause] = hit["fingerprint"]
        assert hit["stop_cause"] == cause
    assert len(set(fps.values())) == len(fps), fps
    assert fps["unknown"] == "gateway-9aaf151a15"
    # an older coach __init__ (kept by a long-running backend) hands handle() a crash hit with no cause
    store = core.Store(tmp_path / "coach")
    bare = sh.resume_signal(CRASH_NOTE, 1800)
    sh.handle(bare, "s9", store, evidence="x", m="shadow", filer=lambda d, store=None, m=None: None)
    (det,) = events(tmp_path)
    assert det.get("stop_cause") == "unknown"


def test_investigation_brief_shows_stop_evidence():
    hit = sh.resume_signal(CRASH_NOTE, 1800, stop={"cause": "plugin reload", "host": "laptop-1",
                                                   "evidence": "2026-11-04 03:50:12 mods-reload: x"})
    brief = sh.investigation_brief({**hit, "session_id": "s1", "host": "laptop-1"},
                                   previous={"fix_card": "t_x", "fixed_at": T0})
    assert "Stop evidence (captured on laptop-1)" in brief and "mods-reload: x" in brief


def test_incident_replay_one_case_per_stop_cause(tmp_path):
    """Replay eval: a crash note's recorded stop_cause gives its own symptom; none keeps the old one."""
    ir = load("selfheal_incident_replay_stop", ROOT / "eval" / "selfheal_incident_replay.py")
    d = tmp_path / "data"
    d.mkdir()
    cases = [(f"c{i}", c) for i, c in enumerate(sh.STOP_CAUSES)] + [("n", None)]
    (d / "turns.jsonl").write_text("".join(json.dumps({"sid": s, "idx": 0, "text": CRASH_NOTE if s != "z" else "hi",
                                                       "prev_reply": "w"}) + "\n" for s, _ in cases + [("z", None)]))
    (d / "turns2.jsonl").write_text("".join(json.dumps({"sid": s, "idx": 0, "prev_log": [], "gap_s": 1800,
                                                        **({"stop_cause": c, "stop_evidence": "e"} if c else {})})
                                            + "\n" for s, c in cases))
    (d / "episodes.json").write_text(json.dumps([{"eid": "Z", "sid": "z", "first": 0, "last": 0,
                                                  "turns": [{"sid": "z", "idx": 0}]}]))
    (d / "fivewhys.jsonl").write_text(json.dumps({"eid": "Z", "real_incident": True}) + "\n")
    llm, _ = judge(0.0)
    ir.run(type("A", (), {"hermes_root": "", "data": str(d), "out": str(tmp_path / "o"), "limit": 0, "workers": 1,
                          "tag": "t"})(), llm=llm)
    res = ir.score(d, ir.jl(tmp_path / "o" / "verdicts.jsonl"), 0.65, 0.65, False, relabel=False)
    fired = {f["sid"]: f["sym"] for f in res["fps"]}
    for s, c in cases:
        assert fired[s] == (f"turn interrupted (backend stopped: {c})" if c else "turn interrupted (backend stopped)")

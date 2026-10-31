"""self-heal lane: every entry path reaches file_defect; dedupe, recurrence, budget, bb routing, shadow vs live,
Inbox outcomes (Do it -> fix card, Not now, Never), digest backstop, queue drain."""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_lane_test", ROOT / "self_heal.py")
core = load("coach_core_test_lane", ROOT / "core.py")
plugin = load("coach_plugin_test_lane", ROOT / "__init__.py")

NOW = 1792660000.0  # 2026-10-22 ~09:06 UTC
HUB = {}  # no hub configured = this host files locally (the hub itself)
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "why": ""}


class Runner:
    """Fake subprocess.run: records argv; `hermes kanban ... create` returns a new task id."""

    def __init__(self, outcomes=None, fail=False):
        self.calls, self.n, self.outcomes, self.fail = [], 0, outcomes or {}, fail

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append((list(argv), input))
        if self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if argv[:3] == ["hermes", "inbox", "outcome"]:
            key = argv[3]
            o = self.outcomes.get(key)
            return SimpleNamespace(returncode=0 if o else 1, stdout=json.dumps(o or {}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a, _ in self.calls if "create" in a]


def opt(argv, flag):
    return argv[argv.index(flag) + 1]


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def defect(component="desktop", symptom="sidebar sessions reshuffle", **kw):
    d = {"component": component, "symptom": symptom, "source": "complaint", "kind": "hermes_bug", "p_defect": 0.9,
         "session_id": "20261022_090000_abcdef", "quote": "i think the sort is broken", "host": "macbook",
         "at": NOW - 60}
    d.update(kw)
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


@pytest.fixture
def store(tmp_path):
    return core.Store(tmp_path / "coach")


def judge(p=0.9, component="desktop", symptom="sidebar sessions reshuffle", kind="admission"):
    def llm(system, prompt):
        return json.dumps({"p_defect": p, "kind": kind, "component": component, "symptom": symptom})
    return llm


# ── shadow vs live ──
def test_shadow_logs_would_file_with_full_card_args_and_creates_nothing(store):
    r = Runner()
    out = sh.file_defect(defect(), store=store, m="shadow", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "would_file" and not r.calls
    (w,) = ev(store, "selfheal_would_file")
    a = w["args"]
    assert a["idempotency_key"] == f"selfheal:{w['fingerprint']}" and a["model"] == "claude-opus-5-5"
    assert a["max_runtime"] == 3600 and a["skills"] == ["five-whys", "hermes-mods-repo"]
    assert a["created_by"] == "self-heal" and a["board"] is None
    for part in ("What happened", "Why", "Plan", "Undo", "event proposed", "@session:",
                 "i think the sort is broken", "LIVE", "C10"):
        assert part in a["body"], part
    assert "hermes inbox post" not in a["body"]  # proposals never reach the Inbox (auto-approved)
    assert not sh.LaneState(store.root).read()  # shadow leaves no lane state


def test_live_on_hub_creates_the_card_with_the_spec_args(store):
    r = Runner()
    d = defect()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    (argv,) = r.creates()
    assert out == {"action": "filed", "card": "t_00000001", "key": f"selfheal:{d['fingerprint']}", "scheduled": None}
    assert opt(argv, "--idempotency-key") == f"selfheal:{d['fingerprint']}"
    assert opt(argv, "--model") == "claude-opus-5-5" and opt(argv, "--max-runtime") == "3600"
    assert opt(argv, "--provider") == "anthropic"  # hub default provider is openai-codex: a bare opus model is refused
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--skill"] == ["five-whys", "hermes-mods-repo"]
    assert "--board" not in argv[:4]
    (f,) = ev(store, "selfheal_filed")
    assert f["card"] == "t_00000001" and f["fingerprint"] == d["fingerprint"] and f["recurrence"] is False
    assert sh.LaneState(store.root).get(d["fingerprint"])["status"] == "filed"


def test_live_off_hub_forwards_to_the_hub_over_ssh(store):
    r = Runner()
    cfg = {"hub_ssh": "user@hub.example", "hub_host": "the-hub"}
    out = sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW, cfg=cfg, budget_fn=OPEN)
    ((argv, stdin),) = r.calls
    assert out["action"] == "forwarded" and argv[0] == "ssh" and "user@hub.example" in argv
    assert argv[-1].endswith("self_heal.py file --mode live")
    assert json.loads(stdin)["symptom"] == "sidebar sessions reshuffle"
    assert not r.creates() and not sh.LaneState(store.root).read()  # the hub decides, not this host


def test_hub_ssh_pins_the_key_with_identities_only(store, tmp_path, monkeypatch):
    # launchd jobs offer different default identities than the shell, and the hub refuses them preauth.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert "-i" not in sh.hub_ssh_argv({})  # no default key on disk: plain ssh
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519").write_text("x")
    argv = sh.hub_ssh_argv({})
    assert argv[argv.index("-i") + 1] == str(tmp_path / ".ssh" / "id_ed25519") and "IdentitiesOnly=yes" in argv
    argv = sh.hub_ssh_argv({"hub_ssh_key": "~/.ssh/hub_key"})
    assert argv[argv.index("-i") + 1] == str(tmp_path / ".ssh" / "hub_key")
    r = Runner()
    sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW,
                   cfg={"hub_ssh": "user@hub.example", "hub_host": "the-hub"}, budget_fn=OPEN)
    ((argv, _),) = r.calls
    assert "IdentitiesOnly=yes" in argv and argv.index("-i") < argv.index("user@hub.example")


def test_hub_cli_file_reads_stdin(store, monkeypatch, capsys):
    monkeypatch.setattr(sh, "file_defect", lambda d, m=None: {"action": "would_file", "fp": d["fingerprint"],
                                                              "m": m})
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=lambda: json.dumps(defect())))
    assert sh.main(["file", "--mode", "live"]) == 0
    assert json.loads(capsys.readouterr().out)["m"] == "live"


def test_off_mode_files_nothing(store):
    assert sh.file_defect(defect(), store=store, m="off", runner=Runner(), now=NOW, cfg=HUB)["action"] == "skip"


@pytest.mark.parametrize("failure", ["missing", "import", "snapshot", "cards"])
def test_budget_unavailable_never_creates_investigation_or_fix(store, monkeypatch, failure):
    def broken(*args, **kwargs):
        raise OSError("injected accounting failure")

    eb = SimpleNamespace(
        snapshot=lambda **kw: {"lanes": {"selfheal": 0}, "caps": {"selfheal": 25}},
        check=lambda *args: (True, ""), engine_cards=lambda *args: [])
    if failure in ("snapshot", "cards"):
        setattr(eb, "snapshot" if failure == "snapshot" else "engine_cards", broken)
    monkeypatch.setattr(sh, "_budget_mod", broken if failure == "import" else
                        lambda: None if failure == "missing" else eb)
    b = sh.budget(now=NOW)
    assert b["available"] is False
    assert not b["ok_usd"] and not b["ok_running"]
    r = Runner()
    d = defect()
    result = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB)
    assert result["action"] == "error" and "budget" in result["why"]
    assert not r.calls and not sh.LaneState(store.root).read()
    sh.LaneState(store.root).update(d["fingerprint"], status="proposed", component="desktop")
    result = sh.file_fix(d["fingerprint"], store=store, runner=r, now=NOW)
    assert result["action"] == "error" and not r.calls
    assert sh.LaneState(store.root).get(d["fingerprint"])["status"] == "proposed"
    assert len(ev(store, "selfheal_budget_unavailable")) == 2


def test_budget_unavailable_shadow_remains_observable(store, monkeypatch):
    monkeypatch.setattr(sh, "_budget_mod", lambda: None)
    r = Runner()
    result = sh.file_defect(defect(), store=store, m="shadow", runner=r, now=NOW, cfg=HUB)
    assert result["action"] == "would_file" and not r.calls
    assert ev(store, "selfheal_would_file")[0]["budget_available"] is False


# ── dedupe ──
def test_one_filing_per_fingerprint_per_day_and_none_while_open(store):
    r = Runner()
    d = defect()
    sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    again = sh.file_defect(defect(symptom="sessions in the sidebar reshuffled"), store=store, m="live", runner=r,
                           now=NOW + 60, cfg=HUB, budget_fn=OPEN)
    assert again["action"] == "skip" and "today" in again["why"]
    nextday = sh.file_defect(d, store=store, m="live", runner=r, now=NOW + 86400, cfg=HUB, budget_fn=OPEN)
    assert nextday["action"] == "skip" and nextday["why"].startswith("open: filed")
    assert len(r.creates()) == 1


# ── recurrence ──
def test_recurrence_of_a_fixed_fingerprint_says_previous_fix_didnt_hold(store):
    r = Runner()
    d = defect()
    fp = d["fingerprint"]
    sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    sh.record_event("proposed", fp, "t_00000001", store=store, now=NOW + 100)
    sh.file_fix(fp, store=store, runner=r, now=NOW + 200, budget_fn=OPEN)
    sh.record_event("fixed", fp, "t_00000002", store=store, now=NOW + 300)
    out = sh.file_defect({**d, "at": NOW + 3 * 86400 - 60}, store=store, m="live", runner=r, now=NOW + 3 * 86400,
                         cfg=HUB, budget_fn=OPEN)  # it happens again 3 days after the fix
    argv = r.creates()[-1]
    assert out["key"] == f"selfheal:{fp}:r2" and opt(argv, "--idempotency-key") == f"selfheal:{fp}:r2"
    body = opt(argv, "--body")
    assert "previous fix t_00000002 didn't hold" in body and "detection-level guard" in body
    assert "(came back)" in argv[argv.index("create") + 1]
    (rec,) = ev(store, "selfheal_recurrence")
    assert rec["previous"] == "t_00000002"
    # its fix card gets a distinct key that coach_weekly still maps to the same fingerprint
    sh.record_event("proposed", fp, out["card"], store=store, now=NOW + 3 * 86400 + 10)
    fx = sh.file_fix(fp, store=store, runner=r, now=NOW + 3 * 86400 + 20, budget_fn=OPEN)
    assert fx["key"] == f"selfheal:{fp}:r2:fix"
    weekly = load("coach_weekly_lane_test", REPO / "skills/productivity/hermes-coach/scripts/coach_weekly.py")
    for k in (out["key"], fx["key"], f"selfheal:{fp}:fix", f"selfheal:{fp}"):
        assert weekly.selfheal_fp(k) == fp and weekly.DEFECT_FP_RX.search(weekly.selfheal_fp(k))


# ── rollout window: "pushed" is not "running on every host" ──
FIXED_AT = 1793163024.0  # agent-664ecedaaf: fix 48913b6339 recorded 2026-10-28 04:50:24Z
SEEN_AT = 1793166654.0   # the MacBook's errors.log line from a process still on old code, 05:50:54Z


def _fixed(store, r, d, fixed_at=FIXED_AT):
    fp = d["fingerprint"]
    sh.file_defect(d, store=store, m="live", runner=r, now=fixed_at - 20000, cfg=HUB, budget_fn=OPEN)
    sh.record_event("proposed", fp, "t_00000001", store=store, now=fixed_at - 19000)
    sh.file_fix(fp, store=store, runner=r, now=fixed_at - 18000, budget_fn=OPEN)
    sh.record_event("fixed", fp, "t_00000002", store=store, now=fixed_at)
    return fp


def test_occurrence_inside_the_rollout_window_is_held_not_filed(store):
    # (a) replay of agent-664ecedaaf: seen 1 h after "fixed" (processed 11 min later), files nothing
    r = Runner()
    d = defect(component="agent", symptom="FileNotFoundError in _execute_child (subprocess.py)", source="signal",
               at=SEEN_AT)
    fp = _fixed(store, r, d)
    n = len(r.creates())
    out = sh.file_defect(d, store=store, m="live", runner=r, now=1793167334.0, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out.get("held") == 1, out
    assert len(r.creates()) == n and not ev(store, "selfheal_recurrence")
    (held,) = ev(store, "selfheal_recurrence_held")
    assert held["fingerprint"] == fp and held["at"] == SEEN_AT and held["held"] == 1
    st = sh.LaneState(store.root).get(fp)
    assert st["status"] == "fixed" and [h["at"] for h in st["held"]] == [SEEN_AT]


def test_occurrence_after_the_rollout_window_still_comes_back_and_lists_held(store):
    # (b) same fingerprint 7 h after the fix files "came back"; the brief names the held occurrence
    r = Runner()
    d = defect(component="agent", symptom="FileNotFoundError in _execute_child (subprocess.py)", source="signal",
               at=SEEN_AT)
    fp = _fixed(store, r, d)
    sh.file_defect(d, store=store, m="live", runner=r, now=SEEN_AT + 60, cfg=HUB, budget_fn=OPEN)
    late = FIXED_AT + 7 * 3600
    out = sh.file_defect({**d, "at": late}, store=store, m="live", runner=r, now=late + 60, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and out["key"] == f"selfheal:{fp}:r2"
    argv = r.creates()[-1]
    assert "(came back)" in argv[argv.index("create") + 1]
    body = opt(argv, "--body")
    assert "Held inside the rollout window" in body and sh._pt(SEEN_AT) in body
    assert ev(store, "selfheal_recurrence")[0]["previous"] == "t_00000002"
    assert not sh.LaneState(store.root).get(fp).get("held")  # the new generation starts clean


def test_window_is_judged_by_occurrence_time_not_processing_time(store):
    # (c) seen inside the window, processed 9 h later (queue drain / hub unreachable): still held
    r = Runner()
    d = defect(component="agent", symptom="FileNotFoundError in _execute_child (subprocess.py)", source="signal",
               at=SEEN_AT)
    _fixed(store, r, d)
    n = len(r.creates())
    out = sh.file_defect(d, store=store, m="live", runner=r, now=FIXED_AT + 9 * 3600, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out.get("held") == 1
    assert len(r.creates()) == n


def test_three_held_occurrences_file_inside_the_window(store):
    # (d) detection guard: a fix that really failed surfaces fast; a replay of the same occurrence never counts twice
    r = Runner()
    d = defect(component="agent", symptom="FileNotFoundError in _execute_child (subprocess.py)", source="signal",
               at=SEEN_AT)
    fp = _fixed(store, r, d)
    n = len(r.creates())
    assert sh.file_defect(d, store=store, m="live", runner=r, now=SEEN_AT + 10, cfg=HUB, budget_fn=OPEN)["held"] == 1
    again = sh.file_defect(d, store=store, m="live", runner=r, now=SEEN_AT + 20, cfg=HUB, budget_fn=OPEN)
    assert again.get("held") == 1  # same occurrence replayed: not a second one
    two = sh.file_defect({**d, "at": SEEN_AT + 600}, store=store, m="live", runner=r, now=SEEN_AT + 610, cfg=HUB,
                         budget_fn=OPEN)
    assert two["held"] == 2 and len(r.creates()) == n
    out = sh.file_defect({**d, "at": SEEN_AT + 1200}, store=store, m="live", runner=r, now=SEEN_AT + 1210, cfg=HUB,
                         budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == n + 1
    body = opt(r.creates()[-1], "--body")
    assert "Held inside the rollout window" in body and sh._pt(SEEN_AT + 600) in body
    assert ev(store, "selfheal_recurrence")[0]["fingerprint"] == fp


def test_deploy_grace_zero_restores_immediate_recurrence(store):
    r = Runner()
    d = defect(component="agent", symptom="FileNotFoundError in _execute_child (subprocess.py)", source="signal",
               at=SEEN_AT)
    _fixed(store, r, d)
    out = sh.file_defect(d, store=store, m="live", runner=r, now=SEEN_AT + 60, cfg={"deploy_grace_hours": 0},
                         budget_fn=OPEN)
    assert out["action"] == "filed"


def test_recurrence_on_the_day_it_was_filed_and_fixed_is_not_swallowed(store):
    # agent-23a0c3b69f: filed, fixed at 05:40Z and back at 08:48Z the same UTC day; the hub logged
    # selfheal_skip "already filed today" and the recurrence only surfaced through a watch card.
    r = Runner()
    d = defect(component="agent", symptom="TimeoutExpired in _run_git (checkpoint_manager.py)", source="signal")
    fp = d["fingerprint"]
    fixed_at = 1793166012.0  # 2026-10-28T05:40:12Z
    sh.file_defect({**d, "at": fixed_at - 7200}, store=store, m="live", runner=r, now=fixed_at - 7000, cfg=HUB,
                   budget_fn=OPEN)
    sh.record_event("proposed", fp, "t_00000001", store=store, now=fixed_at - 6000)
    sh.file_fix(fp, store=store, runner=r, now=fixed_at - 5000, budget_fn=OPEN)
    sh.record_event("fixed", fp, "t_00000002", store=store, now=fixed_at)
    assert sh.LaneState(store.root).get(fp)["filed_day"] == sh._utc_day(fixed_at)
    n = len(r.creates())
    # inside the rollout window: held (counts toward HELD_FILE_AFTER), not skipped as a same-day duplicate
    seen = 1793177310.0  # 08:48:30Z
    held = sh.file_defect({**d, "at": seen}, store=store, m="live", runner=r, now=seen + 60, cfg=HUB, budget_fn=OPEN)
    assert held.get("held") == 1, held
    # after the window, still the same UTC day: a recurrence card
    late = fixed_at + sh.DEPLOY_GRACE_HOURS * 3600 + 600
    assert sh._utc_day(late) == sh._utc_day(fixed_at)
    out = sh.file_defect({**d, "at": late}, store=store, m="live", runner=r, now=late + 60, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and out["key"] == f"selfheal:{fp}:r2", out
    assert len(r.creates()) == n + 1 and "(came back)" in r.creates()[-1][r.creates()[-1].index("create") + 1]
    # and the day cap still holds for that new open card
    again = sh.file_defect({**d, "at": late + 300}, store=store, m="live", runner=r, now=late + 360, cfg=HUB,
                           budget_fn=OPEN)
    assert again["action"] == "skip" and len(r.creates()) == n + 1


# ── budget ──
def test_over_the_dollar_cap_defers_evidence_without_creating_a_card(store):
    r = Runner()
    over = lambda board=None, now=None: {"usd": 25.4, "cap": 25.0, "running": 0, "ok_usd": False,
                                         "ok_running": True, "why": "selfheal lane spend today $25.40 >= $25 cap"}
    out = sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=over)
    assert out["action"] == "deferred"
    assert out["retry_at"] == "2026-10-23T00:00:00Z"
    assert not r.calls  # never create a dispatchable card before checking the cap
    assert not sh.LaneState(store.root).read()
    assert sh.retry_deferred(store, now=NOW, filer=lambda *a, **k: pytest.fail("too early")) == []
    got = []
    sh.retry_deferred(store, now=NOW + 86400,
                      filer=lambda d, **kw: got.append(d) or {"action": "filed"})
    assert got == [defect()]


def test_three_running_cards_defer_new_admission_an_hour_out(store):
    r = Runner()
    busy = lambda board=None, now=None: {"usd": 3.0, "cap": 25.0, "running": 3, "ok_usd": True,
                                         "ok_running": False, "why": ""}
    out = sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=busy)
    assert out["action"] == "deferred" and out["retry_at"] == sh._iso(NOW + 3600)
    assert not r.calls


@pytest.mark.parametrize("denied", ["ok_usd", "ok_running"])
def test_fix_cap_refuses_before_create_and_keeps_approval_retryable(store, denied):
    d = defect()
    lane = sh.LaneState(store.root)
    before = lane.update(d["fingerprint"], status="proposed", component="desktop")
    r = Runner()
    b = {**OPEN(), denied: False}
    result = sh.file_fix(d["fingerprint"], store=store, runner=r, now=NOW,
                         budget_fn=lambda *a: b)
    assert result["action"] == "deferred" and not r.calls
    assert lane.get(d["fingerprint"]) == before


@pytest.mark.parametrize("action", ["error", "deferred"])
def test_deferred_investigation_retries_without_losing_payload(store, action):
    d = defect(details="original evidence", platform="desktop")
    r = Runner()
    b = {**OPEN(), "ok_running": False}
    sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB,
                   budget_fn=lambda *a: b)
    assert not r.calls
    # Failure and renewed denial retain the pending record, then success removes it.
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda *a, **k: {"action": action})
    got = []
    sh.retry_deferred(store, now=NOW + 7201,
                      filer=lambda d, **kw: got.append(d) or {"action": "filed"})
    assert got == [d]
    assert sh.retry_deferred(store, now=NOW + 7202, filer=lambda *a, **k: pytest.fail("duplicate")) == []


def _queue(store, *ds):
    busy = lambda *a: {**OPEN(), "ok_running": False}
    for d in ds:
        sh.file_defect(d, store=store, m="live", runner=Runner(), now=NOW, cfg=HUB, budget_fn=busy)


def test_pending_drains_complaints_then_signals_then_backstop_likelier_first(store):
    sig = defect(symptom="traceback a", source="signal", p_defect=None, at=NOW - 30)
    weak = defect(symptom="weak complaint", source="backstop", p_defect=0.7, at=NOW - 20)
    strong = defect(symptom="strong complaint", source="backstop", p_defect=0.95, at=NOW - 500)
    direct = defect(symptom="direct complaint", source="complaint", p_defect=0.8, at=NOW - 900)
    _queue(store, sig, weak, strong, direct)
    got = []
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda d, **kw: got.append(d["symptom"]) or {"action": "filed"})
    assert got == ["direct complaint", "traceback a", "strong complaint", "weak complaint"]


def test_pending_pass_stops_at_the_first_renewed_denial(store):
    _queue(store, defect(symptom="one"), defect(symptom="two"), defect(symptom="three"))
    calls = []
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda d, **kw: calls.append(1) or {"action": "deferred"})
    assert len(calls) == 1
    left = []
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda d, **kw: left.append(d) or {"action": "filed"})
    assert len(left) == 3  # nothing lost by stopping early


def test_stale_pending_is_dropped_with_a_skip_event_not_filed_late(store):
    old = defect(symptom="old", at=NOW - 8 * 86400)
    fresh = defect(symptom="fresh", at=NOW - 60)
    _queue(store, old, fresh)
    got = []
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda d, **kw: got.append(d["symptom"]) or {"action": "filed"})
    assert got == ["fresh"]
    assert any("stale pending" in e["why"] and e["fingerprint"] == old["fingerprint"]
               for e in ev(store, "selfheal_skip"))
    assert sh.retry_deferred(store, now=NOW + 7200, filer=lambda *a, **k: pytest.fail("resurrected")) == []


@pytest.mark.parametrize("usd,cap", [(float("nan"), 25), (0, float("inf")), (-1, 25), (0, 0)])
def test_invalid_accounting_values_fail_closed(monkeypatch, usd, cap):
    eb = SimpleNamespace(snapshot=lambda **kw: {"lanes": {"selfheal": usd}, "caps": {"selfheal": cap}},
                         check=lambda *a: (True, ""), engine_cards=lambda *a: [])
    monkeypatch.setattr(sh, "_budget_mod", lambda: eb)
    assert sh.budget(now=NOW)["available"] is False


def test_pending_write_failure_never_starts_work(store, monkeypatch):
    def broken(*a):
        raise OSError("injected pending storage failure")
    monkeypatch.setattr(sh, "_pending_db", broken)
    r = Runner()
    with pytest.raises(OSError):
        sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB,
                       budget_fn=lambda *a: {**OPEN(), "ok_running": False})
    assert not r.calls


def test_retry_preserves_newer_detection_and_rechecks_real_gate(store):
    d = defect(details="initial")
    r = Runner()
    busy = lambda *a: {**OPEN(), "ok_running": False}
    sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=busy)
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda d, **kw:
                      sh.file_defect(d, runner=r, cfg=HUB, budget_fn=busy, **kw))
    assert not r.calls
    newer = {**d, "details": "new evidence"}
    def overlapping(d, **kw):
        sh.file_defect(newer, store=store, m="live", runner=r, now=NOW + 7202,
                       cfg=HUB, budget_fn=busy)
        return {"action": "filed"}
    sh.retry_deferred(store, now=NOW + 7202, filer=overlapping)
    got = []
    sh.retry_deferred(store, now=NOW + 10803,
                      filer=lambda d, **kw: got.append(d) or {"action": "filed"})
    assert got == [newer]


@pytest.mark.parametrize("mode", ["off", "shadow", "live"])
def test_cron_retries_pending_only_when_live(store, monkeypatch, mode):
    monkeypatch.setattr(sh, "mode", lambda: mode)
    calls = []
    monkeypatch.setattr(sh, "retry_deferred", lambda *a, **kw: calls.append(1) or [])
    monkeypatch.setattr(sh, "sync", lambda *a, **kw: [])
    monkeypatch.setattr(sh, "drain", lambda *a, **kw: [])
    sh.cron(Path(store.root), store=store, now=NOW, backstop_fn=lambda *a: [])
    assert calls == ([1] if mode == "live" else [])


def test_engine_budget_selfheal_lane_has_its_own_200_cap_outside_the_total(tmp_path):
    eb = load("engine_budget_lane_test", REPO / "skills/productivity/hermes-coach/scripts/engine_budget.py")
    assert eb.CAPS["selfheal"] == 200.0 and "selfheal" in eb.SEPARATE
    snap = {"caps": dict(eb.CAPS), "total_cap": 20.0, "lanes": {"scout": 0, "fixer": 7, "trial": 13, "bot": 0,
                                                                 "selfheal": 10}, "total": 20.0}
    assert eb.check(snap, "selfheal")[0] is True  # engine total is spent, the selfheal lane is not
    assert eb.check(snap, "fixer")[0] is False
    snap["lanes"]["selfheal"] = 200.0
    ok, why = eb.check(snap, "selfheal")
    assert not ok and "selfheal lane" in why
    fx = load("coach_fixer_lane_test", REPO / "skills/productivity/hermes-coach/scripts/coach_fixer.py")
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.execute("create table tasks (idempotency_key text, created_by text)")
    keys = ["selfheal:desktop-0123456789", "selfheal:desktop-0123456789:fix", "selfheal:1-detector",
            "selfheal-fix:kanban-abcdef0123", "coach-fix:x", "selfheal:agent-0123456789:r2"]
    db.executemany("insert into tasks values (?, 'self-heal')", [(k,) for k in keys])
    got = {k for (k,) in db.execute(f"select idempotency_key from tasks t where {fx.LANE_WHERE['selfheal']}")}
    assert got == set(keys) - {"selfheal:1-detector", "coach-fix:x"}
    fixer = {k for (k,) in db.execute(f"select idempotency_key from tasks t where {fx.LANE_WHERE['fixer']}")}
    assert fixer == {"coach-fix:x"}  # self-heal cards never bill the fixer lane


# ── routing ──
def plant_board(root, slug, archived=False):
    d = Path(root) / "kanban" / "boards" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "board.json").write_text(json.dumps({"slug": slug, "archived": archived}))


def test_bb_defects_route_to_the_bb_board_with_bb_self_fix(store, tmp_path):
    plant_board(tmp_path, "bb")
    r = Runner()
    sh.file_defect(defect("bb", "camera enhance shows no sharpening"), store=store, m="live", runner=r, now=NOW,
                   cfg=HUB, budget_fn=OPEN)
    (argv,) = r.creates()
    assert argv[:4] == ["hermes", "kanban", "--board", "bb"]
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--skill"] == ["five-whys", "bb-self-fix"]
    assert sh.route({"component": "bb"}, {"bb_board": "babybear"})["board"] == "babybear"
    assert opt(argv, "--assignee") == "default"


def test_product_defect_creates_its_card_on_the_configured_bb_board(store, tmp_path):
    plant_board(tmp_path, "bb-product")
    r = Runner()
    cfg = {"bb_board": "bb-product"}
    d = defect("bb", "camera enhance shows no sharpening")
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=cfg, budget_fn=OPEN)
    (argv,) = r.creates()
    assert out["action"] == "filed" and argv[:4] == ["hermes", "kanban", "--board", "bb-product"]
    assert opt(argv, "--assignee") == "default"
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--skill"] == ["five-whys", "bb-self-fix"]
    assert sh.LaneState(store.root).get(d["fingerprint"])["board"] == "bb-product"
    assert sh.USD_DAY == 200.0  # cap unchanged


def _pending_fps(store):
    import sqlite3
    p = Path(store.root) / "selfheal_pending.sqlite"
    if not p.exists():
        return []
    db = sqlite3.connect(p)
    try:
        return [json.loads(row[0])["fingerprint"] for row in db.execute("select payload from pending")]
    finally:
        db.close()


def test_missing_bb_board_is_deferred_with_a_log_row_not_dropped(store, tmp_path):
    r = Runner()
    d = defect("bb", "camera enhance shows no sharpening")
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg={"bb_board": "bb"}, budget_fn=OPEN)
    assert out["action"] == "deferred" and "does not exist" in out["why"] and out["board"] == "bb"
    assert out["stop_pass"] is False and not r.creates()
    (row,) = ev(store, "selfheal_deferred")
    assert row["board"] == "bb" and "does not exist" in row["why"] and row["fingerprint"] == d["fingerprint"]
    assert not sh.LaneState(store.root).get(d["fingerprint"]).get("card")
    assert _pending_fps(store) == [d["fingerprint"]]
    # still missing at retry time: kept, not dropped, and it does not block another admission
    other = defect("desktop", "sidebar sessions reshuffle", p_defect=0.4, session_id="other-complaint")
    sh.file_defect(other, store=store, m="live", runner=Runner(), now=NOW, cfg=HUB,
                   budget_fn=lambda *a: {**OPEN(), "ok_running": False})

    def retry(d, **kw):
        return sh.file_defect(d, store=store, m="live", runner=r, now=NOW + 3600,
                              cfg={"bb_board": "bb"}, budget_fn=OPEN)

    sh.retry_deferred(store, now=NOW + 3600, filer=retry)
    assert _pending_fps(store) == [d["fingerprint"]]
    assert r.creates() and "--board" not in r.creates()[0][:4]
    # board appears: the held payload files on it
    plant_board(tmp_path, "bb")
    sh.retry_deferred(store, now=NOW + 3600 + sh.MISSING_BOARD_RETRY_S, filer=retry)
    assert r.creates()[-1][:4] == ["hermes", "kanban", "--board", "bb"]
    assert d["fingerprint"] not in _pending_fps(store)
    assert sh.LaneState(store.root).get(d["fingerprint"])["board"] == "bb"


def test_create_that_reports_a_missing_board_defers_instead_of_dropping(store, tmp_path):
    plant_board(tmp_path, "bb")  # preflight passes; the create still reports the board gone

    class Gone(Runner):
        def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
            self.calls.append((list(argv), input))
            if "create" in argv:
                return SimpleNamespace(returncode=1, stdout="",
                                       stderr="kanban: board 'bb' does not exist. "
                                              "Create it with `hermes kanban boards create bb`.")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    r = Gone()
    d = defect("bb", "wake word misses the second call")
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "deferred" and ev(store, "selfheal_deferred")
    assert not ev(store, "selfheal_filed") and _pending_fps(store) == [d["fingerprint"]]
    assert not sh.LaneState(store.root).get(d["fingerprint"]).get("status")


def test_archived_bb_board_is_treated_as_missing(store, tmp_path):
    plant_board(tmp_path, "bb", archived=True)
    r = Runner()
    out = sh.file_defect(defect("bb", "camera enhance shows no sharpening"), store=store, m="live", runner=r,
                         now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "deferred" and not r.creates() and ev(store, "selfheal_deferred")


def test_missing_bb_board_on_the_fix_path_errors_with_a_log_row(store):
    d = defect("bb", "camera enhance shows no sharpening")
    lane = sh.LaneState(store.root)
    before = lane.update(d["fingerprint"], status="proposed", component="bb", symptom=d["symptom"])
    r = Runner()
    out = sh.file_fix(d["fingerprint"], store=store, runner=r, now=NOW, budget_fn=OPEN)
    assert out["action"] == "error" and "does not exist" in out["why"] and not r.creates()
    assert ev(store, "selfheal_file_error") and lane.get(d["fingerprint"])["status"] == before["status"]


# ── entry paths, through the real hooks / sweeps ──
def test_complaint_hook_reaches_file_defect(store, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    got = []
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=judge(0.9))
    sh_mod = _hooked_self_heal(plugin)
    monkeypatch.setattr(sh_mod, "file_defect", lambda d, store=None, m=None: got.append((d, m)))
    hooks["pre_llm_call"](session_id="s1", user_message="i think the sort is broken")
    hooks["pre_llm_call"](session_id="s2", user_message="the sidebar keeps reshuffling, still broken")
    assert len(got) == 1  # once per fingerprint per day
    d, m = got[0]
    assert m == "shadow" and d["source"] == "complaint" and d["session_id"] == "s1"
    assert d["quote"] == "i think the sort is broken" and d["fingerprint"].startswith("desktop-")


def _hooked_self_heal(plugin_mod):
    """The self_heal module object the plugin's hooks use (loaded by the plugin's own loader)."""
    return plugin_mod._load("self_heal")


def test_admission_hook_reaches_file_defect(store, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    got = []
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True,
                            selfheal_llm=judge(0.95, "agent", "claimed cards scheduled when none were"))
    monkeypatch.setattr(_hooked_self_heal(plugin), "file_defect", lambda d, store=None, m=None: got.append(d))
    hooks["post_llm_call"](session_id="s1", assistant_response="You're right, that was my mistake: no cards were "
                                                               "scheduled.")
    assert len(got) == 1 and got[0]["source"] == "admission" and got[0]["component"] == "agent"


def test_machine_signal_reaches_file_defect_without_a_judge(store, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    calls, got = [], []

    def llm(system, prompt):
        calls.append(1)
        return "{}"
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    monkeypatch.setattr(_hooked_self_heal(plugin), "file_defect", lambda d, store=None, m=None: got.append(d))
    hooks["post_llm_call"](session_id="s1", assistant_response="Sorry — this message was not processed (API call "
                                                               "failed).")
    assert not calls and len(got) == 1 and got[0]["source"] == "signal" and got[0]["kind"] == "api_error"


def test_direct_machine_signal_caller_files_with_details(store):
    """Card 3's sweeper calls file_defect directly with its own fingerprint and details."""
    r = Runner()
    d = {"component": "cron", "symptom": "cron job x failed 3 runs", "source": "signal", "kind": "cron_failed",
         "details": "exit 1: Traceback ... KeyError 'foo'", "host": "hub", "at": NOW}
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    body = opt(r.creates()[0], "--body")
    assert out["action"] == "filed" and "KeyError 'foo'" in body and "hermes cron list" in body
    assert sh.FP_RE.match(out["key"].split(":", 1)[1])


def write_digest(data, host, sid, turns, source="desktop", **kw):
    p = data / host / "default" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": sid, "host": host, "profile": "default", "source": source,
                             "user_turns": [{"t": t, "text": x} for t, x in turns], **kw}))
    return p


def test_digest_backstop_scans_only_new_turns_of_interactive_sessions(store, tmp_path):
    data = tmp_path / "data"
    now = time.time()
    write_digest(data, "macbook", "s1", [(now - 600, "add a login for the room"),
                                         (now - 300, "i think the sort is broken")])
    write_digest(data, "macbook", "old", [(now - 10 * 3600, "still broken!!")])  # before the first-run lookback
    write_digest(data, "macbook", "cron_1", [(now - 100, "still broken")], source="cron")
    write_digest(data, "reports", "x", [(now - 100, "still broken")])
    got = []
    filer = lambda d, store=None, m=None: got.append(d) or {"action": "would_file", "fingerprint": d["fingerprint"]}
    out = sh.backstop(data, store=store, judge=judge(0.9), now=now, filer=filer, m="shadow")
    assert [d["session_id"] for d in got] == ["s1"] and got[0]["source"] == "backstop"
    assert got[0]["quote"] == "add a login for the room" and got[0]["host"] == "macbook" and len(out) == 1
    assert [e["new"] for e in ev(store, "selfheal_detect")] == [True, False]  # same fingerprint: filed once
    assert ev(store, "selfheal_detect")[0]["source"] == "backstop"
    # second pass: nothing new; a new turn in s1 is scanned once
    assert sh.backstop(data, store=store, judge=judge(0.9), now=now + 60, filer=filer, m="shadow") == []
    write_digest(data, "macbook", "s1", [(now - 600, "add a login for the room"),
                                         (now - 300, "i think the sort is broken"),
                                         (now + 30, "the draft got lost again, broken")])
    sh.backstop(data, store=store, judge=judge(0.9, symptom="draft lost on new chat"), now=now + 90, filer=filer,
                m="shadow")
    assert len(got) == 2 and "draft" in got[1]["quote"]


def test_backstop_limit_does_not_advance_cursor_past_unscanned_turns(store, tmp_path):
    """A spent limit must leave later sessions' turns for the next pass (they used to be skipped)."""
    data = tmp_path / "data"
    now = time.time()
    write_digest(data, "macbook", "a", [(now - 500, "still broken, the sort fails")])
    write_digest(data, "macbook", "b", [(now - 400, "still broken, the draft is lost")])
    write_digest(data, "macbook", "c", [(now - 300, "still broken, the tab froze")])
    seen = []
    filer = lambda d, store=None, m=None: seen.append(d["session_id"]) or {"action": "would_file"}
    for i in range(3):
        sh.backstop(data, store=store, judge=judge(0.9, symptom=["sort fails on reload","draft vanishes after restart","tab freezes on scroll"][i]), now=now, filer=filer,
                    m="shadow", limit=1)
    assert sorted(seen) == ["a", "b", "c"]
    assert sh.backstop(data, store=store, judge=judge(0.9), now=now, filer=filer, m="shadow", limit=1) == []


def test_backstop_limit_inside_equal_timestamps_rescans_the_unscanned_turn(store, tmp_path):
    data = tmp_path / "data"
    now = time.time()
    t = now - 200
    write_digest(data, "macbook", "a", [(t, "still broken, the sort fails"), (t, "still broken, the draft is lost")])
    quotes = []
    filer = lambda d, store=None, m=None: quotes.append(d["quote"]) or {"action": "would_file"}
    for i in range(2):
        sh.backstop(data, store=store, judge=judge(0.9, symptom=["sort fails on reload","draft vanishes after restart","tab freezes on scroll"][i]), now=now, filer=filer,
                    m="shadow", limit=1)
    assert len(set(quotes)) == 2


def test_backstop_gives_the_judge_the_prev_reply_and_session_context(store, tmp_path):
    """Digest v6 `prev` (the agent reply before the turn) and the title + earlier user turns reach the judge; without
    them the strict stage read 'why didnt this work [screenshot]' as a new issue (week of 2026-10-12 replay)."""
    data = tmp_path / "data"
    now = time.time()
    p = data / "macbook" / "default" / "s9.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": "s9", "host": "macbook", "profile": "default", "source": "desktop",
                             "title": "Fix sidebar drag", "user_turns": [
                                 {"t": now - 600, "text": "dragging sessions into projects does nothing"},
                                 {"t": now - 300, "text": "why didnt this work [screenshot]",
                                  "prev": "Fixed: drag now moves the session. Reload Desktop to pick it up."}]}))
    prompts = []

    def llm(system, prompt):
        prompts.append(prompt)
        return json.dumps({"p_defect": 0.9, "kind": "agent_fault", "component": "desktop", "symptom": "drag fix failed"})
    sh.backstop(data, store=store, judge=llm, now=now, filer=lambda d, store=None, m=None: {"action": "would_file"},
                m="shadow")
    last = [x for x in prompts if "why didnt this work" in x][0]
    assert "Fixed: drag now moves the session" in last and "Session title: Fix sidebar drag" in last
    assert "dragging sessions into projects does nothing" in last
    first = [x for x in prompts if "USER'S NEW MESSAGE:\ndragging" in x][0]
    assert "first user message of the session" in first


def test_queue_drain_files_each_queued_detection_once(store):
    q = Path(store.root) / "selfheal_queue.jsonl"
    q.parent.mkdir(parents=True, exist_ok=True)
    d = defect()
    q.write_text(json.dumps({"event": "selfheal_detect", "fingerprint": d["fingerprint"], "component": "desktop",
                             "symptom": d["symptom"], "source": "complaint", "session_id": "s1", "evidence": "x",
                             "p": 0.9, "ts": NOW}) + "\n")
    got = []
    filer = lambda d, store=None, m=None: got.append(d) or {"action": "filed"}
    sh.drain(store, filer=filer)
    sh.drain(store, filer=filer)
    assert len(got) == 1 and got[0]["fingerprint"] == d["fingerprint"]


# ── Inbox: Do it -> fix card, Not now, Never ──
def proposed(store, r, d=None):
    d = d or defect()
    d = {**d, "session_id": d["fingerprint"]}  # independent proposal fixtures, not one user turn
    sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    prop = Path(store.root) / "selfheal" / d["fingerprint"] / "proposal.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text("**What happened** x\n**Why** y\n**Plan** add test_sort_stable; guard\n**Undo** git revert")
    sh.record_event("proposed", d["fingerprint"], "t_00000001", store=store, now=NOW + 100)
    return d["fingerprint"]


def test_do_it_creates_the_fix_card_with_red_green_acceptance(store, monkeypatch):
    r = Runner()
    fp = proposed(store, r)
    monkeypatch.setattr(sh, "coach_root", lambda: Path(store.root))
    out = sh.file_fix(fp, store=store, runner=r, now=NOW + 200, budget_fn=OPEN)
    argv = r.creates()[-1]
    assert out["action"] == "filed" and opt(argv, "--idempotency-key") == f"selfheal:{fp}:fix"
    assert opt(argv, "--model") == "claude-opus-5-5"
    assert opt(argv, "--provider") == "anthropic"
    body = opt(argv, "--body")
    for part in ("add test_sort_stable", "FAILING", "PASSING", "Verify live", "event fixed", "--result result.md"):
        assert part in body, part
    (a,) = ev(store, "selfheal_approved")
    assert a["card"] == out["card"] and a["trigger"] == "do_it"
    # idempotent: a second Do it (or the sync backstop) does not file a second fix card
    assert sh.file_fix(fp, store=store, runner=r, now=NOW + 300, budget_fn=OPEN)["action"] == "skip"
    assert len(r.creates()) == 2


def test_inbox_type_do_it_is_a_resolve_the_hub_cron_acts_on():
    """Do it must not be a Kanban `task` action: a worker is fenced from creating cards via the CLI, so the hub
    cron's sync (outside any worker) files the fix card from the resolved outcome."""
    try:
        import hermes_yaml as yaml
    except ModuleNotFoundError as exc:
        if exc.name != "hermes_yaml":
            raise
        import yaml  # Standalone tests; Hermes itself ships hermes_yaml.
    t = yaml.safe_load((REPO / "skills/productivity/hermes-coach/inbox-types/selfheal.yaml").read_text())
    acts = {a["id"]: a for a in t["actions"]}
    assert set(acts) == {"do_it", "not_now", "never"}
    assert {a["kind"] for a in acts.values()} == {"resolve"}
    r = yaml.safe_load((REPO / "skills/productivity/hermes-coach/inbox-types/selfheal-result.yaml").read_text())
    assert {a["id"] for a in r["actions"]} == {"ok", "undo"}


def test_cron_syncs_every_pass_and_backstops_at_most_every_25_minutes(store, monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    synced, scans = [], []
    monkeypatch.setattr(sh, "sync", lambda store=None, now=None, **kw: synced.append(now) or [("fp", "do_it -> t_1")])
    bfn = lambda data, store=None: scans.append(1) or [{"action": "would_file", "fingerprint": "agent-0123456789"}]
    out = sh.cron(tmp_path, store, now=NOW, backstop_fn=bfn)
    assert out == ["inbox: fp do_it -> t_1", "backstop: would_file agent-0123456789"]
    sh.cron(tmp_path, store, now=NOW + 300, backstop_fn=bfn)
    sh.cron(tmp_path, store, now=NOW + 25 * 60, backstop_fn=bfn)
    assert len(synced) == 3 and len(scans) == 2
    monkeypatch.setenv("COACH_SELFHEAL", "off")
    assert sh.cron(tmp_path, store, now=NOW + 3600, backstop_fn=bfn) == [] and len(synced) == 3


def test_sync_do_it_backstop_not_now_and_never(store):
    r = Runner()
    a = proposed(store, r)
    b = proposed(store, r, defect("kanban", "card parked without reason"))
    c = proposed(store, r, defect("agent", "asked user to run terminal command"))
    r.outcomes = {f"selfheal:{a}": {"state": "resolved", "outcome": "do_it"},
                  f"selfheal:{b}": {"state": "resolved", "outcome": "not_now"},
                  f"selfheal:{c}": {"state": "resolved", "outcome": "never"}}
    fixed = []
    done = dict(sh.sync(store, runner=r, now=NOW + 500, auto=False,
                        fixer=lambda fp, **kw: fixed.append(fp) or {**sh.file_fix(fp, budget_fn=OPEN, **kw),
                                                                     "card": "t_fix"}))
    assert fixed == [a] and done[a] == "do_it -> t_fix" and done[b] == "not_now" and done[c] == "never"
    assert {e["how"] for e in ev(store, "selfheal_declined")} == {"not_now", "never"}
    # Never suppresses the fingerprint for good; Not now snoozes it 14 days
    r2 = Runner()
    later = NOW + 30 * 86400
    assert sh.file_defect(defect("agent", "asked user to run terminal command"), store=store, m="live", runner=r2,
                          now=later, cfg=HUB, budget_fn=OPEN)["why"] == "suppressed (Never)"
    snoozed = sh.file_defect(defect("kanban", "card parked without reason"), store=store, m="live", runner=r2,
                             now=NOW + 86400, cfg=HUB, budget_fn=OPEN)
    assert snoozed["action"] == "skip" and "snoozed" in snoozed["why"]
    back = sh.file_defect(defect("kanban", "card parked without reason"), store=store, m="live", runner=r2,
                          now=later, cfg=HUB, budget_fn=OPEN)
    assert back["action"] == "filed" and back["key"].endswith(":r2")
    # acted once: a second sync pass does nothing
    assert sh.sync(store, runner=r, now=NOW + 600, auto=False, fixer=lambda fp, **kw: fixed.append(fp) or {}) == []


# ── auto-approve (default): no proposal reaches the Inbox; only the "Fixed:" result does ──
def test_auto_approve_files_the_fix_with_no_inbox_decision(store, monkeypatch):
    monkeypatch.delenv("COACH_SELFHEAL_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(sh, "coach_root", lambda: Path(store.root))
    r = Runner()
    fp = proposed(store, r)
    assert sh.auto_approve({}) is True
    done = dict(sh.sync(store, runner=r, now=NOW + 500,
                        fixer=lambda fp, **kw: sh.file_fix(fp, budget_fn=OPEN, **kw)))
    assert done[fp].startswith("auto -> t_")
    st = sh.LaneState(store.root).get(fp)
    assert st["status"] == "approved" and opt(r.creates()[-1], "--idempotency-key") == f"selfheal:{fp}:fix"
    (a,) = ev(store, "selfheal_approved")
    assert a["trigger"] == "auto"
    assert not [c for c, _ in r.calls if c[:3] == ["hermes", "inbox", "act"]]  # nothing open to resolve
    assert sh.sync(store, runner=r, now=NOW + 600, fixer=lambda fp, **kw: 1 / 0) == []  # acted once


def test_auto_approve_resolves_an_open_legacy_proposal_and_respects_not_now(store):
    r = Runner()
    a = proposed(store, r)
    b = proposed(store, r, defect("kanban", "card parked without reason"))
    r.outcomes = {f"selfheal:{a}": {"state": "open", "outcome": None, "session_id": "20261028_000000_aaaaaa"},
                  f"selfheal:{b}": {"state": "resolved", "outcome": "not_now"}}
    fixed = []
    done = dict(sh.sync(store, runner=r, now=NOW + 500, auto=True,
                        fixer=lambda fp, **kw: fixed.append((fp, kw["trigger"])) or {"card": "t_fix"}))
    assert fixed == [(a, "auto")] and done[a] == "auto -> t_fix" and done[b] == "not_now"
    acts = [c for c, _ in r.calls if c[:3] == ["hermes", "inbox", "act"]]
    assert acts == [["hermes", "inbox", "act", "20261028_000000_aaaaaa", "do_it"]]


def test_auto_approve_ignores_an_earlier_generations_decision(store):
    b = Board()
    fp, ids = lifecycle(store, b, ["file", "proposed", "declined", "file", "proposed"])
    b.outcomes = {f"selfheal:{fp}": {"state": "resolved", "outcome": "not_now", "at": NOW + 600}}  # gen 1's card
    done = dict(sh.sync(store, runner=b, now=NOW + 30 * 86400, auto=True,
                        fixer=lambda fp, **kw: sh.file_fix(fp, budget_fn=OPEN, **kw)))
    assert done[fp].startswith("auto -> t_") and len(b.creates()) == 3


def test_auto_approve_config_and_env(monkeypatch):
    monkeypatch.delenv("COACH_SELFHEAL_AUTO_APPROVE", raising=False)
    assert sh.auto_approve({}) and not sh.auto_approve({"auto_approve": False})
    monkeypatch.setenv("COACH_SELFHEAL_AUTO_APPROVE", "0")
    assert not sh.auto_approve({"auto_approve": True})


def test_investigation_brief_posts_nothing_to_the_inbox():
    b = sh.investigation_brief({"fingerprint": "fp1", "component": "agent", "symptom": "x", "at": NOW})
    assert "hermes inbox post" not in b and "Post NOTHING to the Inbox" in b and "event proposed" in b
    fx = sh.fix_brief("agent-0123456789", {"component": "agent"})
    assert "hermes inbox post --type" not in fx and "--result result.md" in fx and "Do it" not in fx
    assert "do NOT run `hermes inbox post`" in fx


def test_not_a_defect_closes_without_inbox_and_is_not_refiled_for_a_week(store):
    r = Runner()
    d = defect()
    sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    sh.record_event("not_defect", d["fingerprint"], "t_00000001", note="user setting", store=store, now=NOW + 50)
    assert ev(store, "selfheal_not_defect")[0]["note"] == "user setting"
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW + 2 * 86400, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and "not a defect" in out["why"]


def test_file_errors_are_logged_and_fail_open(store):
    out = sh.file_defect(defect(), store=store, m="live", runner=Runner(fail=True), now=NOW, cfg=HUB,
                         budget_fn=OPEN)
    assert out["action"] == "error" and ev(store, "selfheal_file_error")
    assert not sh.LaneState(store.root).read()


def test_tests_never_exec_for_real(store):
    with pytest.raises(RuntimeError):
        sh._run(["hermes", "kanban", "create", "x"])


def test_mode_from_env_then_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(sh, "coach_root", lambda: tmp_path / "coach")
    monkeypatch.delenv("COACH_SELFHEAL", raising=False)
    assert sh.mode() == "shadow"
    (tmp_path / "coach").mkdir()
    (tmp_path / "coach" / "selfheal-config.json").write_text(json.dumps({"mode": "live"}))
    assert sh.mode() == "live"
    monkeypatch.setenv("COACH_SELFHEAL", "off")
    assert sh.mode() == "off"


def test_investigation_brief_carries_live_replay_lessons():
    """The live-investigator replay (42 incidents) missed roots by stopping at the surface that showed the bad state,
    by trusting the detector's trigger over the user's complaint, and by not naming what the incident host's state
    it could not read. The brief must keep those rules, and the C11/C12/C10 checklists the writer-side checks."""
    d = {"fingerprint": "fp1", "component": "gateway", "symptom": "status chip stuck", "session_id": "s1", "at": NOW}
    b = sh.investigation_brief(d)
    assert "name 2-3 candidate roots" in b
    assert "not the surface that displayed it" in b
    assert "only readable ON that host" in b
    assert "Only after you\n   checked the user's complaint itself" in b
    assert "HERMES_DELEGATED_CHILD_CONTEXT" in b and "reapers" in b
    assert "kanban_notify_subs" in sh.investigation_brief({**d, "component": "kanban"})
    assert "code path that WROTE it" in sh.investigation_brief({**d, "component": "desktop"})


# ── card keys across lifecycles (review round 1) ──
class Board(Runner):
    """Fake board with kanban_db.create_task's idempotency rule: a create whose key matches ANY non-archived card
    (done included) returns that card. `show --json` reports each card's status."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.keys, self.status = {}, {}

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        if "create" in argv:
            self.calls.append((argv, input))
            k = opt(argv, "--idempotency-key")
            tid = self.keys.get(k)
            if not tid or self.status.get(tid) == "archived":
                self.n += 1
                tid = self.keys[k] = f"t_{self.n:08x}"
                self.status[tid] = "ready"
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")
        if "show" in argv:
            self.calls.append((argv, input))
            tid = argv[argv.index("show") + 1]
            if tid not in self.status:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"no such task: {tid}")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"id": tid, "status": self.status[tid]}}),
                                   stderr="")
        return super().__call__(argv, capture_output, text, input, timeout)

    def finish(self, tid, status="done"):
        self.status[tid] = status


def lifecycle(store, board, steps, d=None):
    """Run file / fix / <event> steps 9 days apart (past every dedupe/snooze window); finish each card when its
    lifecycle step is reported. Returns the card id of every file/fix step."""
    d = d or defect()
    fp, t, ids = d["fingerprint"], NOW, []
    for s in steps:
        t += 9 * 86400 if s == "file" else 600
        if s == "file":
            r = sh.file_defect({**d, "at": t - 60}, store=store, m="live", runner=board, now=t, cfg=HUB,
                               budget_fn=OPEN)  # each filing is a fresh occurrence, seen just before it is processed
        elif s == "fix":
            r = sh.file_fix(fp, store=store, runner=board, now=t, budget_fn=OPEN)
        else:
            st = sh.LaneState(store.root).get(fp)
            sh.record_event(s, fp, store=store, now=t)
            board.finish(st["fix_card"] if s in ("fixed", "reverted") else st["card"])
            continue
        assert r["action"] == "filed", (s, r)
        ids.append(r["card"])
    return fp, ids


def test_refile_after_a_reverted_fix_gets_a_new_fix_card_on_do_it(store):
    """file -> proposed -> Do it -> reverted -> refile -> proposed -> Do it used to get the DONE first fix card back
    from `selfheal:<fp>:fix`, log selfheal_approved, and run no fix."""
    b = Board()
    fp, ids = lifecycle(store, b, ["file", "proposed", "fix", "reverted", "file", "proposed", "fix"])
    assert len(set(ids)) == 4 and len(b.creates()) == 4, ids
    keys = [opt(a, "--idempotency-key") for a in b.creates()]
    assert keys == [f"selfheal:{fp}", f"selfheal:{fp}:fix", f"selfheal:{fp}:r2", f"selfheal:{fp}:r2:fix"]
    assert sh.LaneState(store.root).get(fp)["fix_card"] == ids[-1]


def test_recurrence_after_a_refile_runs_a_new_previous_fix_didnt_hold_investigation(store):
    """file -> declined -> refile (:r2) -> Do it -> fixed -> recurrence used to reuse `:r2`, get the done refile card
    back, and leave the fingerprint stuck in `filed`."""
    b = Board()
    fp, ids = lifecycle(store, b, ["file", "proposed", "declined", "file", "proposed", "fix", "fixed", "file"])
    assert len(set(ids)) == 4, ids
    last = b.creates()[-1]
    assert opt(last, "--idempotency-key") == f"selfheal:{fp}:r3"
    assert f"previous fix {ids[2]} didn't hold" in opt(last, "--body")
    st = sh.LaneState(store.root).get(fp)
    assert st["status"] == "filed" and st["card"] == ids[3] and st["gen"] == 3
    # and its own Do it gets a fresh fix card too
    sh.record_event("proposed", fp, store=store, now=NOW + 40 * 86400)
    fx = sh.file_fix(fp, store=store, runner=b, now=NOW + 40 * 86400 + 10, budget_fn=OPEN)
    assert fx["key"] == f"selfheal:{fp}:r3:fix" and fx["card"] not in ids
    weekly = load("coach_weekly_gen_test", REPO / "skills/productivity/hermes-coach/scripts/coach_weekly.py")
    assert {weekly.selfheal_fp(opt(a, "--idempotency-key")) for a in b.creates()} == {fp}


def test_a_key_that_returns_an_earlier_card_moves_to_the_next_generation(store):
    """Lane files from before `gen` (or any drift) can name a key whose card exists: never accept it as new."""
    b = Board()
    d = defect()
    fp = d["fingerprint"]
    b.keys[f"selfheal:{fp}:r2"] = "t_0000beef"
    b.status["t_0000beef"] = "done"
    sh.LaneState(store.root).update(fp, status="declined", card="t_0000beef", closed_at=NOW - 30 * 86400)
    out = sh.file_defect(d, store=store, m="live", runner=b, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and out["card"] != "t_0000beef" and out["key"] == f"selfheal:{fp}:r3"
    assert ev(store, "selfheal_key_collision")[0]["card"] == "t_0000beef"
    # a fix key that hands back a known card is an error, never an approval
    sh.record_event("proposed", fp, store=store, now=NOW + 10)
    b.keys[f"selfheal:{fp}:r3:fix"] = "t_0000beef"
    fx = sh.file_fix(fp, store=store, runner=b, now=NOW + 20, budget_fn=OPEN)
    assert fx["action"] == "error" and not ev(store, "selfheal_approved")
    assert sh.LaneState(store.root).get(fp)["status"] == "proposed"


def test_sync_ignores_an_earlier_generations_decision_on_the_same_inbox_key(store):
    b = Board()
    fp, ids = lifecycle(store, b, ["file", "proposed", "declined", "file", "proposed"])
    b.outcomes = {f"selfheal:{fp}": {"state": "resolved", "outcome": "do_it", "at": NOW + 600}}  # gen 1's card
    assert sh.sync(store, runner=b, now=NOW + 30 * 86400, auto=False) == []
    assert len(b.creates()) == 2


# ── a card that ends without reporting (review round 1) ──
@pytest.mark.parametrize("ending", ["done", "archived", "missing"])
def test_sync_marks_a_card_that_ended_without_posting_stale_and_the_next_detection_refiles(store, ending):
    b = Board()
    d = defect()
    fp = d["fingerprint"]
    first = sh.file_defect(d, store=store, m="live", runner=b, now=NOW, cfg=HUB, budget_fn=OPEN)["card"]
    assert sh.sync(store, runner=b, now=NOW + 300) == []  # still running: left alone
    if ending == "missing":
        del b.status[first]
    else:
        b.finish(first, ending)
    done = dict(sh.sync(store, runner=b, now=NOW + 600))
    assert done[fp].startswith("stale (filed")
    (s,) = ev(store, "selfheal_stale")
    assert s["card"] == first and s["was"] == "filed" and s["kanban"] == ending
    again = sh.file_defect(d, store=store, m="live", runner=b, now=NOW + 86400, cfg=HUB, budget_fn=OPEN)
    assert again["action"] == "filed" and again["card"] != first and again["key"] == f"selfheal:{fp}:r2"


def test_an_approved_fix_card_that_ended_without_fixed_goes_stale_and_refiles(store):
    b = Board()
    fp, ids = lifecycle(store, b, ["file", "proposed", "fix"])
    b.finish(ids[1], "archived")
    assert dict(sh.sync(store, runner=b, now=NOW + 20 * 86400))[fp].startswith("stale (approved")
    out = sh.file_defect(defect(), store=store, m="live", runner=b, now=NOW + 21 * 86400, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and out["card"] not in ids


def test_unreadable_card_status_never_marks_stale(store):
    r = Runner()  # `show` output is not JSON: status unknown
    sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert sh.reconcile(store, runner=r, now=NOW + 600) == []
    assert sh.reconcile(store, runner=Runner(fail=True), now=NOW + 600) == []


def _write_proposal(store, fp, mtime):
    p = Path(store.root) / "selfheal" / fp / "proposal.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("title\n**What happened**\n")
    os.utime(p, (mtime, mtime))
    return p


def test_done_investigation_whose_event_was_lost_but_wrote_a_proposal_reconciles_to_proposed(store):
    """plugin-129c2f2518: the card's `event proposed` went to a scratch lane; its proposal.md is the evidence."""
    b = Board()
    d = defect()
    fp = d["fingerprint"]
    card = sh.file_defect(d, store=store, m="live", runner=b, now=NOW, cfg=HUB, budget_fn=OPEN)["card"]
    _write_proposal(store, fp, NOW + 300)
    b.finish(card, "done")
    done = dict(sh.reconcile(store, runner=b, now=NOW + 600))
    assert done[fp].startswith("proposed")
    st = sh.LaneState(store.root).get(fp)
    assert st["status"] == "proposed" and st["proposed_at"] == NOW + 300
    (p,) = ev(store, "selfheal_proposed")
    assert p["via"] == "reconcile" and p["card"] == card
    assert ev(store, "selfheal_stale") == []
    # and the next sync pass files its fix card
    fixed = []
    out = dict(sh.sync(store, runner=b, now=NOW + 900, auto=True,
                       fixer=lambda f, **kw: fixed.append(f) or {"card": "t_0000f1f1"}))
    assert fixed == [fp] and out[fp] == "auto -> t_0000f1f1"


@pytest.mark.parametrize("proposal", ["missing", "older generation"])
def test_done_investigation_without_a_fresh_proposal_still_goes_stale(store, proposal):
    b = Board()
    d = defect()
    fp = d["fingerprint"]
    card = sh.file_defect(d, store=store, m="live", runner=b, now=NOW, cfg=HUB, budget_fn=OPEN)["card"]
    if proposal == "older generation":
        _write_proposal(store, fp, NOW - 86400)
    b.finish(card, "done")
    assert dict(sh.reconcile(store, runner=b, now=NOW + 600))[fp].startswith("stale (filed")


def test_archived_investigation_with_a_proposal_is_not_resurrected(store):
    b = Board()
    d = defect()
    fp = d["fingerprint"]
    card = sh.file_defect(d, store=store, m="live", runner=b, now=NOW, cfg=HUB, budget_fn=OPEN)["card"]
    _write_proposal(store, fp, NOW + 300)
    b.finish(card, "archived")
    assert dict(sh.reconcile(store, runner=b, now=NOW + 600))[fp].startswith("stale (filed")


# ── origin session already queued a fix card ──
def _owner(cards):
    return lambda sid, since: sorted((c for c in cards if c["created_at"] >= since), key=lambda c: -c["created_at"])


def test_session_linked_active_card_is_adopted_not_filed(store):
    r = Runner()
    d = defect()
    cards = [{"id": "t_aaaa1111", "status": "ready", "created_at": NOW - 120, "board": None},
             {"id": "t_bbbb2222", "status": "running", "created_at": NOW - 30, "board": None}]
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN, owner_fn=_owner(cards))
    assert out["action"] == "skip" and out["adopted"] and out["card"] == "t_bbbb2222"
    assert not r.creates()
    st = sh.LaneState(store.root).get(d["fingerprint"])
    assert st["status"] == "approved" and st["fix_card"] == "t_bbbb2222"
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["card"] == "t_bbbb2222"
    again = sh.file_defect(d, store=store, m="live", runner=r, now=NOW + 10, cfg=HUB, budget_fn=OPEN)
    assert again["action"] == "skip" and not r.creates()


def test_card_from_before_the_complaint_or_unrelated_still_files(store):
    r = Runner()
    old = [{"id": "t_old00000", "status": "running", "created_at": NOW - 60 - sh.ADOPT_LOOKBACK_S - 5, "board": None}]
    out = sh.file_defect(defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN,
                         owner_fn=_owner(old))
    assert out["action"] == "filed" and len(r.creates()) == 1
    r2 = Runner()
    out = sh.file_defect(defect(symptom="other thing", quote="a separate issue", at=NOW + 3600), store=store, m="live", runner=r2, now=NOW, cfg=HUB,
                         budget_fn=OPEN, owner_fn=lambda sid, since: [])
    assert out["action"] == "filed"


def test_session_owned_cards_reads_creator_and_subscription(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "kanban.db")
    db.executescript("""create table tasks(id text, title text, status text, created_at int, session_id text);
        create table kanban_notify_subs(task_id text, chat_id text);
        insert into tasks values('t_1','a','running',100,'S'),('t_2','b','done',100,'S'),('t_3','c','ready',200,NULL),
          ('t_4','d','ready',50,'S'),('t_5','e','todo',300,'X');
        insert into kanban_notify_subs values('t_3','S');""")
    db.commit()
    db.close()
    got = sh.session_owned_cards("S", 90, home=tmp_path)
    assert [c["id"] for c in got][0] == "t_3" and {c["id"] for c in got} == {"t_1", "t_2", "t_3"}  # done counts; t_4 predates, t_5 other session


# ── adoption sees other hosts' cards (via the origin digest) and finished (review/done) cards ──
def _real_adoption_env(monkeypatch, tmp_path):
    """Run the real owner lookup (no owner_fn) against a throwaway HERMES_HOME and coach-data dir."""
    monkeypatch.delenv("COACH_SELFHEAL_NO_EXEC", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_DATA", str(tmp_path / "data"))
    (tmp_path / "home").mkdir(exist_ok=True)


def _turns_digest(tmp_path, sid, turns, last_reply=None):
    p = tmp_path / "data" / "macbook" / "default" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": sid, "host": "macbook", "profile": "default", "source": "desktop",
                             "user_turns": turns, "last_reply": last_reply}))


def test_remote_host_card_named_in_the_origin_digest_after_the_complaint_is_adopted(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    d = defect(at=NOW - 60, host="macbook")
    _turns_digest(tmp_path, d["session_id"], [
        {"t": NOW - 500, "text": "earlier work", "prev": "see t_0000aaaa1111 for the old thing"},
        {"t": NOW - 60, "text": "this panel makes no sense", "prev": "ok"},
        {"t": NOW - 30, "text": "next", "prev": "I filed t_01a17f83116946fd for the panel fix"}])
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out["adopted"] and out["card"] == "t_01a17f83116946fd"
    assert not r.creates()
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["board"] == "macbook:default" and e["card"] == "t_01a17f83116946fd"


@pytest.mark.parametrize("status", ["done", "review"])
def test_local_card_in_review_or_done_is_adopted(store, tmp_path, monkeypatch, status):
    import sqlite3
    _real_adoption_env(monkeypatch, tmp_path)
    d = defect()
    db = sqlite3.connect(tmp_path / "home" / "kanban.db")
    db.executescript("create table tasks(id text, title text, status text, created_at int, session_id text);"
                     "create table kanban_notify_subs(task_id text, chat_id text);")
    db.execute("insert into tasks values('t_ffff0001','fix','%s',?,?)" % status, (int(NOW - 30), d["session_id"]))
    db.commit()
    db.close()
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out["adopted"] and out["card"] == "t_ffff0001"
    assert not r.creates()


def test_digest_card_mentioned_only_before_the_complaint_is_not_adopted(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    d = defect(at=NOW - 60, host="macbook")
    _turns_digest(tmp_path, d["session_id"], [
        {"t": NOW - 500, "text": "earlier", "prev": "see t_0000aaaa1111 for the old thing"},
        {"t": NOW - 60, "text": "this panel makes no sense", "prev": "t_0000bbbb2222 is done"}],
        last_reply=None)
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 1


# ── no origin session (background signal): adopt an open card that already owns the same error signature ──
SIG_TB = ("WARNING hermes_cli.kanban_session_activity: kanban activity unavailable; rows marked kanban_unknown\n"
          "Traceback (most recent call last):\n"
          '  File "~/.hermes/hermes-agent/hermes_cli/kanban_session_activity.py", line 145, in _read_counts\n'
          '    if "session_id" not in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}:\n'
          "sqlite3.OperationalError: disk I/O error\n")


def _signal_defect(**kw):
    return defect(component="kanban", source="signal", session_id="", quote="", details=SIG_TB,
                  symptom="OperationalError in _read_counts (kanban_session_activity.py): kanban activity unavailable",
                  **kw)


def _board(tmp_path, rows):
    import sqlite3
    db = sqlite3.connect(tmp_path / "home" / "kanban.db")
    db.executescript("create table tasks(id text, title text, body text, status text, created_at int, session_id text);"
                     "create table kanban_notify_subs(task_id text, chat_id text);")
    db.executemany("insert into tasks values(?,?,?,?,?,NULL)", rows)
    db.commit()
    db.close()


def test_sessionless_defect_adopts_open_card_with_matching_signature(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [("t_0a0e00000001", "Kanban reads fail: 'disk I/O error' after WAL deleted",
                       "every _read_counts call raises sqlite3.OperationalError: disk I/O error until restart",
                       "scheduled", int(NOW - 86400))])
    d = _signal_defect()
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out["adopted"] and out["card"] == "t_0a0e00000001"
    assert not r.creates()
    st = sh.LaneState(store.root).get(d["fingerprint"])
    assert st["status"] == "approved" and st["fix_card"] == "t_0a0e00000001"
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["via"] == "signature" and e["card"] == "t_0a0e00000001"


def test_open_card_with_same_exception_in_another_function_is_not_adopted(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [("t_0a0e00000002", "Inbox store: disk I/O error",
                       "sqlite3.OperationalError: disk I/O error in _write_outcome (inbox_store.py)",
                       "running", int(NOW - 600))])
    r = Runner()
    out = sh.file_defect(_signal_defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 1
    assert not ev(store, "selfheal_adopted_owner")


def test_done_card_with_matching_signature_is_not_adopted(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [("t_0a0e00000003", "Kanban reads fail: disk I/O error in _read_counts",
                       "sqlite3.OperationalError: disk I/O error (kanban_session_activity.py)",
                       "done", int(NOW - 600))])
    r = Runner()
    out = sh.file_defect(_signal_defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 1
    assert not ev(store, "selfheal_adopted_owner")


def test_signature_adoption_is_injectable_and_skips_known_cards(store):
    d = _signal_defect()
    seen = []
    fn = lambda sig: seen.append(sig) or [{"id": "t_0a0e00000004", "status": "todo", "created_at": NOW, "board": "x"}]
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN, sig_owner_fn=fn)
    assert out["adopted"] and out["card"] == "t_0a0e00000004" and not r.creates()
    assert seen[0]["exc_msg"] == "disk I/O error" and seen[0]["func"] == "_read_counts"
    assert seen[0]["file"] == "kanban_session_activity.py"


# ── one root, several faces: Kanban DB read failures from a stale per-process view are one family ──
CORRUPT_TB = ("Traceback (most recent call last):\n"
              '  File "~/.hermes/hermes-agent/hermes_cli/kanban_db_connect.py", line 561, in _guard_existing_db_is_healthy\n'
              "    raise KanbanDbCorruptError(path, detail)\n"
              "hermes_cli.kanban_db_connect.KanbanDbCorruptError: Refusing to open corrupt kanban DB at "
              "/home/ubuntu/.hermes/kanban.db: integrity_check returned '*** in database main ***\\n"
              "Tree 52 page 1997: Rowid 1997 out of order'\n")


def _corrupt_signal(**kw):
    return defect(component="kanban", source="signal", session_id="", quote="", details=CORRUPT_TB,
                  symptom="KanbanDbCorruptError in _guard_existing_db_is_healthy (kanban_db_connect.py): kanban_show failed",
                  **kw)


def test_replay_kanban_disk_io_then_corrupt_signal_adopts_the_first_lane(store):
    """Replay of 2026-10-27: kanban-759104eb56 (disk I/O error) filed; kanban-7fbb819bc1 (KanbanDbCorruptError,
    no session) must join that lane's card instead of opening a second investigation of the same root."""
    r = Runner()
    first = sh.file_defect(_signal_defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert first["action"] == "filed" and len(r.creates()) == 1
    owner = first["card"]
    second_d = _corrupt_signal(at=NOW + 2000)
    second = sh.file_defect(second_d, store=store, m="live", runner=r, now=NOW + 2040, cfg=HUB, budget_fn=OPEN)
    assert len(r.creates()) == 1, "second face of the same Kanban root filed its own card"
    assert second["action"] == "skip" and second["adopted"] and second["card"] == owner
    assert second["via"] == "family"
    (e,) = [x for x in ev(store, "selfheal_adopted_owner") if x.get("via") == "family"]
    assert e["card"] == owner and e["family"] == "kanban-db-view"
    st = sh.LaneState(store.root).get(second["fingerprint"])
    assert st["status"] == "approved" and st["fix_card"] == owner


def test_unreliable_view_error_joins_an_open_corrupt_lane(store):
    r = Runner()
    first = sh.file_defect(_corrupt_signal(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert first["action"] == "filed"
    d = defect(component="kanban", source="signal", session_id="", quote="",
               symptom="KanbanDbUnreliableViewError in connect (kanban_db_connect.py): board view unreliable",
               details="hermes_cli.kanban_db_connect.KanbanDbUnreliableViewError: unreliable view of ~/.hermes/kanban.db\n")
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW + 600, cfg=HUB, budget_fn=OPEN)
    assert len(r.creates()) == 1 and out["adopted"] and out["card"] == first["card"]


def test_unrelated_kanban_error_is_not_in_the_db_view_family(store):
    r = Runner()
    sh.file_defect(_signal_defect(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    d = defect(component="kanban", source="signal", session_id="", quote="",
               symptom="KeyError in _render_card (kanban_cli.py): 'assignee'",
               details="KeyError: 'assignee'\n")
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW + 600, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 2
    assert sh.root_family(d) is None


def test_disk_io_error_outside_kanban_is_not_in_the_family():
    d = defect(component="inbox", symptom="OperationalError in _write_outcome (inbox_store.py): write failed",
               details="sqlite3.OperationalError: disk I/O error\n")
    assert sh.root_family(d) is None
    assert sh.root_family(_signal_defect()) == "kanban-db-view"
    assert sh.root_family(_corrupt_signal()) == "kanban-db-view"


def test_a_fixed_family_lane_is_not_adopted(store):
    r = Runner()
    d1 = _signal_defect()
    sh.file_defect(d1, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    sh.LaneState(store.root).update(d1["fingerprint"], status="fixed", fixed_at=NOW + 100)
    out = sh.file_defect(_corrupt_signal(), store=store, m="live", runner=r, now=NOW + 600, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 2


def test_fixed_result_is_stored_quietly_and_creates_no_inbox_item(store, tmp_path):
    """A result never becomes an Inbox item: it lands in <coach>/selfheal-results/<fp>.md and the log."""
    fp = "agent-0123456789"
    res = tmp_path / "result.md"
    res.write_text("Fixed: x\nred/green proof")
    called = []
    orig = sh._run
    sh._run = lambda argv, *a, **k: called.append(argv) or (0, "", "")
    try:
        sh.record_event("fixed", fp, card="t_abc", note="sha1", store=store, result=str(res))
    finally:
        sh._run = orig
    assert (Path(store.root) / "selfheal-results" / f"{fp}.md").read_text() == res.read_text()
    assert not [c for c in called if "inbox" in c]
    (e,) = [x for x in ev(store, "selfheal_fixed")]
    assert e["result_file"].endswith(f"{fp}.md")
    with pytest.raises(ValueError):
        sh.record_event("proposed", fp, store=store, result=str(res))


# ── in-flight reservation + serialized admission (go-live review: $268 spent against a $200 cap) ──
def _eb(usd, cap, cards, spent=0.0):
    import re

    def check(snap, lane, extra=0.0):
        t = snap["lanes"][lane] + extra
        return (False, f"{lane} lane spend today ${t:.2f} >= ${cap:g} cap") if t >= cap else (True, "")

    return SimpleNamespace(
        snapshot=lambda **kw: {"lanes": {"selfheal": usd}, "caps": {"selfheal": cap}},
        check=check, engine_cards=lambda home, b, st: [c for c in cards if c["status"] in st],
        card_budget=lambda c, snap: float(re.search(r"<= \$(\d+)", c["body"]).group(1)),
        card_cost=lambda home, b, tid: spent)


def test_budget_reserves_inflight_and_new_card(monkeypatch):
    cards = [{"id": f"t_{i}", "lane": "selfheal", "status": s, "body": "Budget for this card <= $8."}
             for i, s in enumerate(["running", "running", "ready"])]
    monkeypatch.setattr(sh, "_budget_mod", lambda: _eb(usd=170.0, cap=200.0, cards=cards))
    b = sh.budget(now=NOW)  # 170 spent < 200, but 3 x $8 in flight + $8 new = 32 -> 202 >= 200
    assert b["available"] and b["usd"] == 170.0 and b["reserved"] == 32.0
    assert not b["ok_usd"] and "reserved" in b["why"] and b["running"] == 2
    monkeypatch.setattr(sh, "_budget_mod", lambda: _eb(usd=170.0, cap=200.0, cards=cards[:1]))
    assert sh.budget(now=NOW)["ok_usd"]  # 170 + 8 + 8 = 186
    monkeypatch.setattr(sh, "_budget_mod", lambda: _eb(usd=170.0, cap=200.0, cards=cards[:1], spent=7.0))
    assert sh.budget(now=NOW, new_card_usd=sh.FIX_BUDGET)["reserved"] == 16.0  # 1 unspent + 15 fix


def test_concurrent_admissions_cannot_both_take_the_last_headroom(store, monkeypatch):
    import threading
    created, gate = [], threading.Event()

    def budget_fn(board=None, now=None):
        ok = not created  # headroom for exactly one more card
        gate.wait(0.3)  # widen the read->create window the lock must close
        return {"usd": 1.0, "cap": 25.0, "running": len(created), "ok_usd": ok, "ok_running": True, "why": "full"}

    def runner(argv, **kw):
        if "create" in argv:
            created.append(argv)
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{len(created):08x}"}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    outs = []
    ds = [defect(component=c, symptom=f"distinct symptom {c}", session_id=c) for c in ("desktop", "kanban")]
    ts = [threading.Thread(target=lambda d=d: outs.append(
        sh.file_defect(d, store=store, m="live", runner=runner, now=NOW, cfg=HUB, budget_fn=budget_fn)))
        for d in ds]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(created) == 1
    assert sorted(o["action"] for o in outs) == ["deferred", "filed"]


def test_file_fix_reserves_fix_budget_by_default(store, monkeypatch):
    seen = []
    monkeypatch.setattr(sh, "budget", lambda board=None, now=None, new_card_usd=None: (
        seen.append(new_card_usd) or {"usd": 1, "cap": 200, "running": 0, "ok_usd": False, "ok_running": True,
                                      "available": True, "why": "full", "reserved": 0}))
    d = defect()
    sh.LaneState(store.root).update(d["fingerprint"], status="proposed", component="desktop")
    r = sh.file_fix(d["fingerprint"], store=store, runner=Runner(), now=NOW)
    assert seen == [sh.FIX_BUDGET] and r["action"] == "deferred"


# ── the origin session fixed it with a bare commit (no card): record fixed, file nothing ──
def _fork_repo_with_commit(tmp_path, subject, ts):
    import subprocess
    repo = tmp_path / "fork"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_AUTHOR_DATE": f"{int(ts)} +0000", "GIT_COMMITTER_DATE": f"{int(ts)} +0000"}
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env=env)
    g("init", "-q", "-b", "main")
    (repo / "f").write_text("x")
    g("add", "f")
    g("commit", "-q", "-m", subject)
    g("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, g("rev-parse", "HEAD").stdout.strip()


def _commit_case(store, tmp_path, monkeypatch, subject, commit_at, last_reply="Fixed. The icon now maps to cloud."):
    _real_adoption_env(monkeypatch, tmp_path)
    d = defect(at=NOW - 60, host="macbook", component="desktop", complaint_at=NOW - 3600)
    repo, sha = _fork_repo_with_commit(tmp_path, subject, commit_at)
    monkeypatch.setenv("COACH_FORK_REPO", str(repo))
    p = tmp_path / "data" / "macbook" / "default" / f"{d['session_id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": d["session_id"], "host": "macbook", "user_turns": [], "last_reply": last_reply,
                             "last_activity_at": NOW - 3000}))
    r = Runner()
    return d, sha, r, sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)


def test_defect_fixed_by_bare_session_commit_files_nothing_and_records_fixed(store, tmp_path, monkeypatch):
    d, sha, r, out = _commit_case(store, tmp_path, monkeypatch,
                                  "fix(desktop): show SSH cloud gateways with the cloud glyph", NOW - 3200)
    assert out["action"] == "skip" and out["adopted"] and out["commit"] == sha
    assert not r.creates()
    assert sh.LaneState(store.root).get(d["fingerprint"] if d.get("fingerprint") else sh.fingerprint("desktop", d["symptom"]))["status"] == "fixed"
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["via"] == "session_commit" and e["commit"] == sha
    assert sha[:12] in ev(store, "selfheal_fixed")[0]["note"]


def test_commit_before_the_complaint_or_other_component_is_not_a_fix(store, tmp_path, monkeypatch):
    _, _, r, out = _commit_case(store, tmp_path, monkeypatch, "fix(desktop): old", NOW - 7200)
    assert out["action"] == "filed" and len(r.creates()) == 1


def test_commit_for_another_component_is_not_a_fix(store, tmp_path, monkeypatch):
    _, _, r, out = _commit_case(store, tmp_path, monkeypatch, "fix(kanban): unrelated", NOW - 3200)
    assert out["action"] == "filed" and len(r.creates()) == 1


def test_window_commit_touching_no_traceback_file_is_not_adopted(store, tmp_path, monkeypatch):
    """A time-window `fix(<component>)` commit that touches none of the traceback's files is not the fix."""
    _real_adoption_env(monkeypatch, tmp_path)
    d = defect(at=NOW - 60, host="macbook", component="desktop", complaint_at=NOW - 3600,
               details='ERROR x: boom\nTraceback (most recent call last):\n  File "/h/gateway/session_store.py", line 3, in f')
    repo, sha = _fork_repo_with_commit(tmp_path, "fix(desktop): unrelated sessions change", NOW - 3200)
    monkeypatch.setenv("COACH_FORK_REPO", str(repo))
    p = tmp_path / "data" / "macbook" / "default" / f"{d['session_id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": d["session_id"], "host": "macbook", "user_turns": [], "last_reply": "ok",
                             "last_activity_at": NOW - 3000}))
    assert sh.session_commit_fix(d, NOW - 3600, data=tmp_path / "data", repo=repo) is None
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and not ev(store, "selfheal_adopted_owner")
    # the same commit, when its diff does touch a traceback file, is adopted
    d2 = {**d, "details": d["details"].replace("session_store.py", "f")}
    assert sh.session_commit_fix(d2, NOW - 3600, data=tmp_path / "data", repo=repo)["sha"] == sha


# ── cards filed before the no-Inbox rule keep their old `hermes inbox post` step (defect plugin-0001f0af0f) ──
class BodyBoard(Board):
    """Board whose `show --json` also returns each card's body."""

    def __init__(self, bodies, **kw):
        super().__init__(**kw)
        self.bodies = bodies

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        if "show" in argv:
            self.calls.append((argv, input))
            tid = argv[argv.index("show") + 1]
            if tid not in self.status:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"no such task: {tid}")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {
                "id": tid, "status": self.status[tid], "body": self.bodies.get(tid, "")}}), stderr="")
        return super().__call__(argv, capture_output, text, input, timeout)


OLD_STEP = ('6. Post the result: `env HERMES_HOME="$HOME/.hermes" hermes inbox post --type selfheal-result '
            '--field fp=x --key selfheal:x --poster "self-heal" --body - "Fixed: <what>" < <result.md>`')


def test_sync_comments_once_on_an_open_card_that_still_says_inbox_post(store):
    b = BodyBoard({})
    fp, ids = lifecycle(store, b, ["file", "proposed", "fix"])
    b.bodies[ids[1]] = "Self-heal FIX\n" + OLD_STEP
    done = dict(sh.sync(store, runner=b, now=NOW + 600))
    assert done[fp] == f"inbox step noted on {ids[1]}"
    comments = [a for a, _ in b.calls if "comment" in a]
    assert len(comments) == 1 and comments[0][-2] == ids[1] and "IGNORE the `hermes inbox post`" in comments[0][-1]
    sh.sync(store, runner=b, now=NOW + 900)  # once per card
    assert len([a for a, _ in b.calls if "comment" in a]) == 1
    assert ev(store, "selfheal_inbox_step_noted")[0]["ok"] is True


def test_sync_leaves_current_briefs_and_ended_cards_alone(store):
    b = BodyBoard({})
    fp, ids = lifecycle(store, b, ["file", "proposed", "fix"])
    current = sh.fix_brief(fp, sh.LaneState(store.root).get(fp)) + sh.investigation_brief(defect())
    assert "hermes inbox post" in current  # the current briefs name the command only to forbid it
    # ...and an approved proposal may quote the command as its live check (t_01a1850ff844f91d got a false note)
    current += "\n- Live check: run `hermes inbox post --poster self-heal --type selfheal-result ...`, confirm refused."
    b.bodies[ids[1]] = current
    sh.sync(store, runner=b, now=NOW + 600)
    b.bodies[ids[1]] = OLD_STEP
    b.finish(ids[1], "done")
    sh.sync(store, runner=b, now=NOW + 900)
    assert not [a for a, _ in b.calls if "comment" in a]


# ── deferred kanban signals recheck the trigger card (self-heal kanban-40fa92de39) ──
def _kanban_home(tmp_path, monkeypatch, status, board="worker-load", card="t_01a181f353b47ebf"):
    import sqlite3
    home = tmp_path / "hh"
    db = home / "kanban" / "boards" / board / "kanban.db"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.execute("create table tasks (id text primary key, title text, status text)")
    c.execute("insert into tasks values (?,?,?)", (card, "load with-worker #23", status))
    c.commit()
    c.close()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(sh, "host_name", lambda: "hubhost")
    return db


def kanban_defect(card="t_01a181f353b47ebf", board="worker-load", **kw):
    """The payload signals.to_defect produced for the 10-28 worker-load card (no structured kanban key)."""
    base = dict(symptom=f"kanban card {card} on hubhost/{board} failed 3x (crashed): load with-worker #23",
                source="signal", kind="kanban", p_defect=None, host="hubhost", session_id=None, quote="",
                details=f"board {board} card {card} status=triage title=load with-worker #23\noutcomes: crashed")
    return defect(component="kanban", **{**base, **kw})


def test_pending_kanban_signal_whose_card_was_archived_is_skipped_not_filed(store, tmp_path, monkeypatch):
    db = _kanban_home(tmp_path, monkeypatch, "triage")
    d = kanban_defect()
    _queue(store, d)
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("update tasks set status='archived'")  # the owner closed it while the row waited
    c.commit()
    c.close()
    out = sh.retry_deferred(store, now=NOW + 3601, filer=lambda *a, **k: pytest.fail("filed a closed card"))
    assert out and out[0]["action"] == "skip"
    skips = [e for e in ev(store, "selfheal_skip") if e["fingerprint"] == d["fingerprint"]]
    assert skips and "trigger card t_01a181f353b47ebf now archived" in skips[-1]["why"]
    assert sh.retry_deferred(store, now=NOW + 7200, filer=lambda *a, **k: pytest.fail("resurrected")) == []


def test_pending_kanban_signal_whose_card_is_still_in_triage_is_filed(store, tmp_path, monkeypatch):
    _kanban_home(tmp_path, monkeypatch, "triage")
    d = kanban_defect()
    _queue(store, d)
    got = []
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda d, **kw: got.append(d) or {"action": "filed"})
    assert got == [d]


def test_pending_kanban_signal_with_missing_board_or_remote_host_keeps_filing(store, tmp_path, monkeypatch):
    _kanban_home(tmp_path, monkeypatch, "archived")
    gone = kanban_defect(board="nope", card="t_aaaa")
    remote = kanban_defect(host="macbook", symptom="remote kanban card")  # another host's board: unreadable here
    _queue(store, gone, remote)
    got = []
    sh.retry_deferred(store, now=NOW + 3601, filer=lambda d, **kw: got.append(d["symptom"]) or {"action": "filed"})
    assert sorted(got) == sorted([gone["symptom"], remote["symptom"]])


def test_live_file_defect_skips_a_kanban_signal_whose_cards_are_all_closed(store, tmp_path, monkeypatch):
    _kanban_home(tmp_path, monkeypatch, "done")
    r = Runner()
    d = {**kanban_defect(), "kanban": {"board": "worker-load", "cards": ["t_01a181f353b47ebf"]}}
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and "now done" in out["why"] and not r.creates()


def test_fork_history_is_isolated_from_the_real_fork(tmp_path):
    """session_commit_fix must not read ~/.hermes/hermes-agent from tests: a real upstream `fix(desktop)` commit
    (736354aa74fa, 2026-10-22 09:16Z) sits inside the NOW window and made the not-adopted digest test skip."""
    assert sh._fork_repo() == tmp_path / "no-fork-repo"
    assert sh.session_commit_fix(defect(at=NOW - 60, host="macbook"), NOW - 60, data=tmp_path) is None

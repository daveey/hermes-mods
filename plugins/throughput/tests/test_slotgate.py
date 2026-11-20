"""The slot gate: a person-set token cap, a wait-adjusted tokens-per-slot estimate, and card starts held at the
slot count this gateway's share of the cap pays for. Hand-computed fixtures."""
import json
import os

import pytest

from throughput_under_test import slotgate as S
from throughput_under_test import throttle as T
from throughput_under_test.store import Store

NOW = 1_800_000_030
END = NOW // 60 * 60
CARD = dict(task_id="t_1", lane="ready", assignee="default", board=None)


def _store(root, me="w1"):
    root.mkdir(parents=True, exist_ok=True)
    st = Store(root / "series.sqlite")
    st.set_meta("me", me)
    return st


def _fill(st, me, *, tokens_per_min, busy, held_per_min, minutes=15, end=END):
    for k in range(1, minutes + 1):
        m = end - 60 * k
        st.put(me, "tokens_io", [(m, tokens_per_min)])
        st.put(me, S.BUSY_METRIC, [(m, busy)])
        st.put(me, S.WAIT_METRIC, [(m, held_per_min)])


class Clock:
    def __init__(self, t=float(NOW)):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


# --- the estimate ---------------------------------------------------------------------------------------------

def test_held_time_is_left_out_of_the_tokens_per_slot_estimate(tmp_path):
    """10 busy slots, 6 of them held all minute: 4 unheld slots burned 120k tokens/min, so one slot costs
    30k tokens/min = 500 tokens/s (not 200, the cost a busy-only estimate would claim)."""
    st = _store(tmp_path)
    _fill(st, "w1", tokens_per_min=120_000, busy=10, held_per_min=360)
    tokens, busy_s, held_s = S.window_totals(st.path, "w1", NOW)
    assert (tokens, busy_s, held_s) == (15 * 120_000, 15 * 600, 15 * 360)
    assert S.raw_estimate(tokens, busy_s, held_s) == pytest.approx(500.0)


def test_no_measurement_under_five_unheld_slot_minutes(tmp_path):
    assert S.raw_estimate(1_000_000, 900, 700) is None  # 200 s unheld
    assert S.raw_estimate(0, 900, 0) is None
    assert S.raw_estimate(30_000, 300, 0) == pytest.approx(100.0)
    assert S.raw_estimate(1000, 600, 900) is None  # held never exceeds busy


def test_the_running_estimate_is_an_exponential_average_with_a_one_hour_half_life():
    assert S.blend(None, None, 300.0, NOW) == 300.0
    assert S.blend(100.0, NOW - 3600, 300.0, NOW) == pytest.approx(200.0)
    assert S.blend(100.0, NOW, 300.0, NOW) == pytest.approx(100.0)


def test_update_stores_and_blends_and_an_old_estimate_expires(tmp_path):
    st = _store(tmp_path)
    _fill(st, "w1", tokens_per_min=120_000, busy=10, held_per_min=360)
    first = S.update(tmp_path, st.path, "w1", NOW)
    assert first["per_s"] == pytest.approx(500.0) and S.read_estimate(tmp_path, NOW)["per_s"] == pytest.approx(500.0)
    # an hour later the slots cost 100 tokens/s: halfway there
    st2 = _store(tmp_path / "b")
    later = NOW + 3600
    _fill(st2, "w1", tokens_per_min=60_000, busy=10, held_per_min=0, end=later // 60 * 60)
    out = S.update(tmp_path, st2.path, "w1", later)
    assert out["per_s"] == pytest.approx(300.0)
    # no fresh measurement: the stored estimate stands, and expires after EXPIRE_S
    assert S.update(tmp_path, tmp_path / "missing.sqlite", "w1", later + 60)["per_s"] == pytest.approx(300.0)
    assert S.read_estimate(tmp_path, later + S.EXPIRE_S + 1) is None


def test_sample_records_busy_slots_and_the_last_minutes_held_seconds(tmp_path):
    st = _store(tmp_path)
    pacer = T.Pacer(tmp_path)
    pacer.note_wait(END - 90, "t_a", 45)
    pacer.note_wait(END - 80, "t_a", 45)  # capped: one card waits at most 60 s a minute
    pacer.note_wait(END - 70, "t_b", 10)
    S.sample(tmp_path, st, "w1", NOW, pacer=pacer, running=7)
    with st._conn() as c:
        rows = dict(((m, k), v) for m, k, v in c.execute(
            "SELECT metric, minute, value FROM minutes WHERE gateway='w1'"))
    assert rows[(S.BUSY_METRIC, END)] == 7.0
    assert rows[(S.WAIT_METRIC, END - 120)] == 70.0  # 60 (t_a, capped) + 10 (t_b)
    assert rows[(S.WAIT_METRIC, END - 60)] == 0.0


def test_sample_stores_nothing_without_a_card_count(tmp_path, monkeypatch):
    st = _store(tmp_path)
    monkeypatch.setattr(S, "active_runs", lambda: None)
    assert S.sample(tmp_path, st, "w1", NOW) is None
    with st._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM minutes").fetchone()[0] == 0


def test_waits_older_than_the_keep_window_are_forgotten(tmp_path):
    pacer = T.Pacer(tmp_path)
    pacer.note_wait(NOW - T.WAIT_KEEP_S - 120, "t_a", 30)
    pacer.note_wait(NOW, "t_b", 5)
    state = json.loads(T.pace_path(tmp_path).read_text())
    assert list(state["waits"]) == [str(int(NOW) // 60)]


def test_a_card_calls_held_seconds_are_counted_while_it_waits(tmp_path, monkeypatch):
    clock = Clock()
    gate = T.Gate(tmp_path, tmp_path / "series.sqlite", sleep=clock.sleep, clock=clock.now)
    T.write_cap(tmp_path, 0, NOW - 10)  # every call holds
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_held")
    assert gate.wait(max_s=20) == pytest.approx(20.0)
    state = json.loads(T.pace_path(tmp_path).read_text())
    assert sum(cell.get("t_held", 0) for cell in state["waits"].values()) == pytest.approx(20.0)


def test_a_chat_wait_is_not_a_card_slot(tmp_path):
    clock = Clock()
    gate = T.Gate(tmp_path, tmp_path / "series.sqlite", sleep=clock.sleep, clock=clock.now)
    T.write_cap(tmp_path, 0, NOW - 10)
    gate.wait(max_s=10)
    assert not T.pace_path(tmp_path).exists() or "waits" not in json.loads(T.pace_path(tmp_path).read_text())


# --- the allowance --------------------------------------------------------------------------------------------

def test_allowance_is_the_share_of_the_cap_over_the_cost_of_a_slot():
    assert S.allowance(None, 0.5, 100.0) is None  # no cap: no limit
    assert S.allowance(0.0, 0.5, 100.0) == 0  # a cap of 0: no slots
    assert S.allowance(1000.0, 0.5, 100.0) == 5
    assert S.allowance(1000.0, 0.5, 300.0) == 1  # floor(1.67)
    assert S.allowance(10.0, 0.5, 300.0) == 1  # at least one while the cap is above 0
    assert S.allowance(1000.0, 0.5, None) == S.UNMEASURED_SLOTS


def test_local_limit_uses_this_gateways_share_and_stored_estimate(tmp_path):
    st = _store(tmp_path, me="w1")
    for k in range(1, 16):  # w1 used 3x what w2 used: share = 0.25/2 + 0.75 * 3/4 = 0.6875
        st.put("w1", "tokens_io", [(END - 60 * k, 3000.0)])
        st.put("w2", "tokens_io", [(END - 60 * k, 1000.0)])
    T._write_json(S.estimate_path(tmp_path), {"per_s": 50.0, "at": NOW - 60})
    out = S.local(tmp_path, st.path, 400.0, NOW)
    assert out["share"] == pytest.approx(0.6875) and out["source"] == "measured"
    assert out["allowed"] == 5  # floor(0.6875 * 400 / 50) = floor(5.5)


# --- the start gate -------------------------------------------------------------------------------------------

def _gate(root, running):
    clock = Clock()
    return T.Gate(root, root / "series.sqlite", sleep=clock.sleep, clock=clock.now, running=lambda: running), clock


def test_start_gate_opens_only_the_allowed_slots(tmp_path):
    st = _store(tmp_path, me="w1")
    st.put("w1", "tokens_io", [(END - 60, 1.0)])  # the only gateway: share 1
    T.write_cap(tmp_path, 300.0, NOW - 10)  # 300 tokens/s
    T._write_json(S.estimate_path(tmp_path), {"per_s": 100.0, "at": NOW - 60})  # 3 slots
    gate, _ = _gate(tmp_path, 1)
    answers = [gate.start_gate(**dict(CARD, task_id=f"t_{n}")) for n in range(4)]
    assert answers[:2] == [None, None]  # running 1 + 2 starts = 3
    assert answers[2:] == [{"hold": True, "reason": "token_slots"}] * 2


def test_start_gate_holds_when_more_cards_run_than_the_cap_pays_for_and_never_stops_them(tmp_path):
    st = _store(tmp_path, me="w1")
    st.put("w1", "tokens_io", [(END - 60, 1.0)])
    T.write_cap(tmp_path, 300.0, NOW - 10)
    T._write_json(S.estimate_path(tmp_path), {"per_s": 100.0, "at": NOW - 60})
    gate, clock = _gate(tmp_path, 15)
    assert gate.start_gate(**CARD) == {"hold": True, "reason": "token_slots"}
    # 15 s later it reads again: still 15 running, still held
    clock.t += T.START_GATE_TTL_S
    assert gate.start_gate(**CARD) == {"hold": True, "reason": "token_slots"}


def test_start_gate_at_a_cap_of_zero_holds_and_without_a_cap_falls_back(tmp_path):
    _store(tmp_path, me="w1")
    T.write_cap(tmp_path, 0, NOW - 10)
    assert _gate(tmp_path, 0)[0].start_gate(**CARD) == {"hold": True, "reason": "token_slots"}
    T.write_cap(tmp_path, None, NOW - 5)  # no cap: no slot limit, and the rate rule has no cap to exceed
    assert _gate(tmp_path, 40)[0].start_gate(**CARD) is None


def test_start_gate_without_a_card_count_uses_the_rate_rule(tmp_path):
    st = _store(tmp_path, me="w1")
    st.put("w1", "tokens_io", [(END - 60, 60_000.0)])  # 1000 tokens/s against a 300 cap: over
    T.write_cap(tmp_path, 300.0, NOW - 10)
    assert _gate(tmp_path, None)[0].start_gate(**CARD) == {"hold": True, "reason": "token_throttle"}


def test_start_gate_fails_open_on_a_broken_read(tmp_path, monkeypatch):
    gate, _ = _gate(tmp_path, 3)
    monkeypatch.setattr(T, "status", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gate.start_gate(**CARD) is None


# --- the person's cap -----------------------------------------------------------------------------------------

def test_a_manual_cap_paces_calls_at_that_cap(tmp_path):
    st = _store(tmp_path, me="w1")
    st.put("w1", "tokens_io", [(END - 60, 1.0)])
    T.write_cap(tmp_path, 200.0, NOW - 10)
    gate = T.Gate(tmp_path, st.path, clock=lambda: float(NOW))
    plan = gate.plan(NOW)
    assert plan[0] == "pace" and plan[1] == pytest.approx(T.DEFAULT_CALL_TOKENS / 200.0)
    assert T.status(tmp_path, st.path, NOW)["cap_per_s"] == 200.0
    assert T.status(tmp_path, st.path, NOW)["user_cap_per_s"] == 200.0


def test_removing_the_cap_without_a_budget_host_stops_throttling(tmp_path):
    T.write_cap(tmp_path, None, NOW - 10)
    gate = T.Gate(tmp_path, tmp_path / "series.sqlite", clock=lambda: float(NOW))
    assert gate.plan(NOW) == ("pass",)
    assert T.status(tmp_path, tmp_path / "series.sqlite", NOW)["cap_per_s"] is None


def test_a_zero_cap_holds_every_call_and_reads_as_100_percent_for_older_gateways(tmp_path):
    T.write_cap(tmp_path, 0, NOW - 10)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["percent"] == 100.0 and data["mode"] == "manual"
    assert T.Gate(tmp_path, tmp_path / "series.sqlite", clock=lambda: float(NOW)).plan(NOW) == ("hold",)


def test_bad_caps_are_refused():
    for bad in (-1, "x", float("nan"), True):
        with pytest.raises(ValueError):
            T.cap_value(bad)


def _v2(total=1000.0):
    return {"computed_at": NOW - 30, "global_rate_tokens_per_s": total,
            "pools": {"anthropic": {"rate_tokens_per_s": total * 0.4, "at_reserve": False},
                      "openai-codex": {"rate_tokens_per_s": total * 0.6, "at_reserve": False}}}


def _pacing(root, total=1000.0):
    path = root / "llm_pacing.json"
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"v2": _v2(total)}))
    return path


def test_the_budget_host_folds_a_persons_cap_in_as_the_lower_of_the_two(tmp_path):
    pacing = _pacing(tmp_path, 1000.0)
    assert T.publish_budget(tmp_path, None, pacing, NOW)  # no person cap yet: the budget alone
    assert T.read_record(tmp_path)["percent"] == 0.0
    T.write_cap(tmp_path, 250.0, NOW + 10)  # a person sets 250 tokens/s (here, or folded from any gateway)
    assert T.publish_budget(tmp_path, None, pacing, NOW + 20)
    level = T.effective(tmp_path, None, NOW + 20)
    assert level["mode"] == "auto" and level["percent"] == pytest.approx(75.0)
    b = T.budget(level, NOW + 20)
    assert b["cap_per_s"] == pytest.approx(250.0)
    assert b["pool_caps"] == {"anthropic": pytest.approx(100.0), "openai-codex": pytest.approx(150.0)}
    assert T.read_user_cap(tmp_path)["cap_per_s"] == 250.0
    assert T.status(tmp_path, tmp_path / "series.sqlite", NOW + 20)["user_cap_per_s"] == 250.0
    # the budget falls under the person's cap: the budget caps, the person's cap is kept for later
    assert T.publish_budget(tmp_path, None, _pacing(tmp_path, 200.0), NOW + 80)
    level = T.effective(tmp_path, None, NOW + 80)
    assert level["percent"] == 0.0 and T.budget(level, NOW + 80)["cap_per_s"] == pytest.approx(200.0)
    assert T.read_user_cap(tmp_path)["cap_per_s"] == 250.0
    # its own write is not taken for a person's setting
    os.utime(T.level_path(tmp_path), (NOW + 80, NOW + 80))
    assert not T.publish_budget(tmp_path, None, _pacing(tmp_path, 200.0), NOW + 90)
    assert T.read_user_cap(tmp_path)["cap_per_s"] == 250.0


def test_a_cap_above_the_budget_leaves_the_budget_in_force_and_none_removes_it(tmp_path):
    pacing = _pacing(tmp_path, 1000.0)
    T.write_cap(tmp_path, 5000.0, NOW)
    T.publish_budget(tmp_path, None, pacing, NOW + 10)
    assert T.effective(tmp_path, None, NOW + 10)["percent"] == 0.0
    T.write_cap(tmp_path, 100.0, NOW + 20)
    T.publish_budget(tmp_path, None, pacing, NOW + 30)
    assert T.effective(tmp_path, None, NOW + 30)["percent"] == pytest.approx(90.0)
    T.write_cap(tmp_path, None, NOW + 40)
    T.publish_budget(tmp_path, None, pacing, NOW + 50)
    assert T.effective(tmp_path, None, NOW + 50)["percent"] == 0.0
    assert T.read_user_cap(tmp_path)["cap_per_s"] is None


def test_an_older_panels_slider_on_the_budget_level_becomes_a_person_cap(tmp_path):
    pacing = _pacing(tmp_path, 1000.0)
    T.publish_budget(tmp_path, None, pacing, NOW)
    level = T.effective(tmp_path, None, NOW + 5)
    T.set_level(tmp_path, level, 40, NOW + 5)  # 40 % off a 1000 tokens/s budget = 600
    T.publish_budget(tmp_path, None, pacing, NOW + 10)
    assert T.read_user_cap(tmp_path)["cap_per_s"] == pytest.approx(600.0)
    assert T.effective(tmp_path, None, NOW + 10)["percent"] == pytest.approx(40.0)


def test_percent_for():
    assert T.percent_for(None, 1000.0) == 0.0
    assert T.percent_for(0.0, 1000.0) == 100.0
    assert T.percent_for(250.0, 1000.0) == 75.0
    assert T.percent_for(2000.0, 1000.0) == 0.0
    assert T.percent_for(250.0, None) == 0.0


# --- review fixes: per-card grants, rollout compatibility, reserve holds, batched wait writes ------------------

def test_asking_twice_about_the_same_card_takes_one_slot(tmp_path):
    """Core's pickup asks whether a card would start, then the claim asks again: one slot, not two."""
    st = _store(tmp_path, me="w1")
    st.put("w1", "tokens_io", [(END - 60, 1.0)])
    T.write_cap(tmp_path, 300.0, NOW - 10)
    T._write_json(S.estimate_path(tmp_path), {"per_s": 100.0, "at": NOW - 60})  # 3 slots
    gate, _ = _gate(tmp_path, 1)
    a = dict(CARD, task_id="t_a")
    b = dict(CARD, task_id="t_b")
    c = dict(CARD, task_id="t_c")
    assert [gate.start_gate(**x) for x in (a, b, a, b)] == [None] * 4  # pickup probe, then the claims
    assert gate.start_gate(**c) == {"hold": True, "reason": "token_slots"}


def test_a_manual_cap_is_never_stored_at_0_percent_so_older_code_enforces_it(tmp_path):
    T.write_cap(tmp_path, 500.0, NOW)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert 0 < data["percent"] < 100 and data["cap_per_s"] == 500.0
    # what code before the manual mode does with it: a slider level whose stored cap is in force
    legacy = {k: v for k, v in data.items() if k != "mode"}
    T.level_path(tmp_path).write_text(json.dumps(legacy))
    assert T.limit_of(T.read_record(tmp_path), None, NOW) == 500.0


def test_a_cap_set_under_a_budget_keeps_reserve_holds_and_scales_the_pools(tmp_path):
    budget_level = {"mode": "auto", "percent": 0.0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 0.0,
                    "openai-codex": 600.0, "xai-oauth": 400.0}, "budget_at": NOW - 60, "set_at": NOW - 30}
    T._write_json(T.level_path(tmp_path), budget_level)
    level = T.effective(tmp_path, None, NOW)
    T.write_cap(tmp_path, 250.0, NOW, level)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["mode"] == "manual" and data["percent"] == pytest.approx(75.0)  # an older publisher keeps 75 %
    assert data["pool_caps"] == {"anthropic": 0.0, "openai-codex": pytest.approx(150.0), "xai-oauth": pytest.approx(100.0)}
    gate = T.Gate(tmp_path, tmp_path / "series.sqlite", clock=lambda: float(NOW))
    assert gate.plan(NOW, "anthropic") == ("hold",)  # still at reserve
    plan = gate.plan(NOW, "openai-codex")
    assert plan[0] == "pace" and plan[3] == "openai-codex" and plan[1] == pytest.approx(T.DEFAULT_CALL_TOKENS / 150.0)
    assert gate.plan(NOW, "other")[0] == "pace"  # an ungoverned provider: the person's global cap
    # "off" under a budget: the budget's pools still cap until the publisher folds it in
    T.write_cap(tmp_path, None, NOW + 1, level)
    assert T.Gate(tmp_path, tmp_path / "series.sqlite", clock=lambda: float(NOW)).plan(NOW + 1, "anthropic") == ("hold",)


def test_a_long_wait_writes_the_pace_file_about_once_a_minute(tmp_path, monkeypatch):
    clock = Clock()
    gate = T.Gate(tmp_path, tmp_path / "series.sqlite", sleep=clock.sleep, clock=clock.now)
    T.write_cap(tmp_path, 0, NOW - 10)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_long")
    writes = []
    real = T.Pacer.note_wait
    monkeypatch.setattr(T.Pacer, "note_wait", lambda self, now, task, s: (writes.append(s), real(self, now, task, s)))
    assert gate.wait(max_s=180) == pytest.approx(180.0)
    assert 3 <= len(writes) <= 4  # 36 polls, one write per minute turned and one at the end
    state = json.loads(T.pace_path(tmp_path).read_text())
    assert sum(cell["t_long"] for cell in state["waits"].values()) == pytest.approx(180.0)


def test_carried_pool_caps_expire_like_the_budget_they_came_from(tmp_path):
    """No publisher folds the cap in: after BUDGET_EXPIRE_S the carried reserve hold ends; the person's cap stays."""
    T._write_json(T.level_path(tmp_path), {"mode": "auto", "percent": 0.0, "cap_per_s": 1000.0,
                                           "pool_caps": {"anthropic": 0.0}, "budget_at": NOW - 60, "set_at": NOW - 30})
    T.write_cap(tmp_path, 250.0, NOW, T.effective(tmp_path, None, NOW))
    gate = T.Gate(tmp_path, tmp_path / "series.sqlite", clock=lambda: float(NOW))
    assert gate.plan(NOW, "anthropic") == ("hold",)
    later = NOW + 2 * 3600
    plan = gate.plan(later, "anthropic")
    assert plan[0] == "pace" and len(plan) == 3 and plan[1] == pytest.approx(T.DEFAULT_CALL_TOKENS / 250.0)


@pytest.mark.parametrize("cap", [250.0, 2000.0])  # below and above a 1000 tokens/s budget
def test_a_burst_set_and_ended_never_moves_the_persons_cap(tmp_path, cap):
    pacing = _pacing(tmp_path, 1000.0)
    T.write_cap(tmp_path, cap, NOW)
    T.publish_budget(tmp_path, None, pacing, NOW + 10)
    assert T.read_user_cap(tmp_path)["cap_per_s"] == cap
    T.set_burst(tmp_path, T.effective(tmp_path, None, NOW + 20), 2, 3600, NOW + 20)
    T.publish_budget(tmp_path, None, pacing, NOW + 30)
    assert T.read_user_cap(tmp_path)["cap_per_s"] == cap
    level = T.effective(tmp_path, None, NOW + 30)
    assert level["burst"]["factor"] == 2.0
    assert T.budget(level, NOW + 30)["cap_per_s"] == pytest.approx(min(cap, 1000.0) * 2)
    assert T.shown_user_cap(tmp_path / "elsewhere", level, NOW + 30) == (None if cap > 1000 else pytest.approx(cap))
    T.clear_burst(tmp_path, T.effective(tmp_path, None, NOW + 40), NOW + 40)
    T.publish_budget(tmp_path, None, pacing, NOW + 50)
    assert T.read_user_cap(tmp_path)["cap_per_s"] == cap
    assert T.budget(T.effective(tmp_path, None, NOW + 50), NOW + 50)["cap_per_s"] == pytest.approx(min(cap, 1000.0))


# --- no limit: a person's override of the budget, and limit_mode ---------------------------------------------

def test_no_limit_overrides_the_budget_until_a_person_chooses_otherwise(tmp_path):
    pacing = _pacing(tmp_path, 1000.0)
    T.publish_budget(tmp_path, None, pacing, NOW)
    assert T.status(tmp_path, tmp_path / "s.sqlite", NOW)["limit_mode"] == "budget"
    T.write_cap(tmp_path, T.UNLIMITED, NOW + 10, T.effective(tmp_path, None, NOW + 10))
    data = json.loads(T.level_path(tmp_path).read_text())
    assert "pool_caps" not in data and 0 < data["percent"] < 1  # no reserve holds carried; older code: a cap never hit
    for t in (NOW + 20, NOW + 80, NOW + 3600):  # the publisher leaves it in force, however often it runs
        assert T.publish_budget(tmp_path, None, pacing, t) is False
    level = T.effective(tmp_path, None, NOW + 3600)
    assert level["mode"] == "manual"
    gate = T.Gate(tmp_path, tmp_path / "s.sqlite", clock=lambda: float(NOW + 3600))
    assert gate.plan(NOW + 3600, "anthropic") == ("pass",) and gate.plan(NOW + 3600) == ("pass",)
    st = T.status(tmp_path, tmp_path / "s.sqlite", NOW + 3600)
    assert st["limit_mode"] == "no_limit" and st["cap_per_s"] is None and st["user_cap_per_s"] is None
    assert S.local(tmp_path, tmp_path / "s.sqlite", st["cap_per_s"], NOW + 3600)["allowed"] is None
    # choosing the budget again: the publisher takes over at once
    T.write_cap(tmp_path, None, NOW + 3700, level)
    fresh = json.loads(pacing.read_text())
    fresh["v2"]["computed_at"] = NOW + 3690  # the hub recomputes its budget every few minutes
    pacing.write_text(json.dumps(fresh))
    assert T.publish_budget(tmp_path, None, pacing, NOW + 3710) is True
    assert T.status(tmp_path, tmp_path / "s.sqlite", NOW + 3710)["limit_mode"] == "budget"


def test_limit_mode_names_what_caps_the_fleet(tmp_path):
    pacing = _pacing(tmp_path, 1000.0)
    st = lambda t: T.status(tmp_path, tmp_path / "s.sqlite", t)["limit_mode"]
    assert st(NOW) == "none"  # nothing set, no budget host
    T.write_cap(tmp_path, 250.0, NOW)
    assert st(NOW) == "limit"
    T.publish_budget(tmp_path, None, pacing, NOW + 10)
    assert st(NOW + 10) == "limit"  # folded: the person's 250 is under the budget
    T.write_cap(tmp_path, 5000.0, NOW + 20, T.effective(tmp_path, None, NOW + 20))
    T.publish_budget(tmp_path, None, pacing, NOW + 30)
    assert st(NOW + 30) == "budget"  # theirs is above the budget: the budget caps
    T.write_cap(tmp_path, 0, NOW + 40)
    assert st(NOW + 40) == "hold"

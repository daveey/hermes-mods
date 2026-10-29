"""session-outcome tests: a fake hermes_state_outcome records calls; the real user-steps ledger under a temp home."""
import importlib.util
import json
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("session_outcome_plugin_under_test", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCore(types.ModuleType):
    """Matches the contract signatures exactly; records every call."""

    def __init__(self):
        super().__init__("hermes_state_outcome")
        self.declared, self.providers, self.judges, self.derived, self.derive_calls = [], {}, [], {}, []

    def declare_outcome(self, sid, outcome):
        if outcome.get("kind") not in ("done", "decision", "waiting", "running", "muted", "unresolved"):
            raise ValueError("bad kind")
        self.declared.append((sid, outcome))

    def derive_outcome(self, sid, *, db=None, ignore_live_turn=False, pending_reply=None):
        """Mimics core precedence: live turn, providers, then the judge (heuristic fallback) on the reply."""
        self.derive_calls.append({"sid": sid, "ignore_live_turn": ignore_live_turn, "pending_reply": pending_reply})
        if not ignore_live_turn:
            return {"kind": "running", "source": "derived"}
        if sid in self.derived:
            return self.derived[sid]
        for fn in self.providers.values():
            got = fn(sid)
            if got and got.get("decision_items"):
                return {"kind": "decision", "items": got["decision_items"]}
        text = pending_reply or ""
        verdict = self.judges[-1](sid, text) if self.judges else None
        if verdict is None:
            verdict = len(text) >= 40 and not text.rstrip().endswith("?")
        return {"kind": "done", "summary": text} if verdict else {"kind": "unresolved", "reason": "no_summary"}

    def refresh_outcome(self, sid, trigger):
        return self.derive_outcome(sid)

    def get_outcome(self, sid):
        return None

    def register_outcome_provider(self, name, fn):
        self.providers[name] = fn

    def register_outcome_judge(self, fn):
        self.judges.append(fn)


class Ctx:
    def __init__(self):
        self.tools, self.hooks = {}, {}

    def register_tool(self, name, **kw):
        self.tools[name] = kw

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # The run's own session env (a kanban worker or one-shot run) must not leak into the surface tests.
    for k in ("HERMES_KANBAN_TASK", "HERMES_SESSION_OUTCOME_OFF", "HERMES_PLATFORM", "HERMES_SESSION_PLATFORM",
              "HERMES_SINGLE_QUERY_SESSION", "HERMES_SESSION_SOURCE_EXPLICIT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("create table sessions (id text primary key, parent_session_id text, end_reason text, source text)")
    db.executemany("insert into sessions values (?,?,?,?)",
                   [("S1", None, None, "desktop"), ("T1", None, None, "tui"), ("C1", None, None, "cli"),
                    ("K1", None, None, "slack"), ("R1", None, None, "cron")])
    db.commit()
    db.close()
    return tmp_path


@pytest.fixture
def fake(monkeypatch):
    core = FakeCore()
    monkeypatch.setitem(sys.modules, "hermes_state_outcome", core)
    return core


@pytest.fixture
def plugin(home, fake):
    mod = _load()
    mod._llm = lambda system, user: "NO"
    mod.register(Ctx())
    fake.providers.clear()
    fake.judges.clear()
    fake.register_outcome_provider("user-steps", mod.provider)
    fake.register_outcome_judge(mod.judge)
    return mod


def call(plugin, args, sid="S1"):
    return json.loads(plugin.handle(args, session_id=sid))


# ---- tool kinds ---------------------------------------------------------------------------------------

def test_done_declares_summary(plugin, fake):
    out = call(plugin, {"kind": "done", "summary": "Shipped  v2; tests pass."})
    assert out["ok"] and out["declared"]
    assert fake.declared == [("S1", {"kind": "done", "summary": "Shipped v2; tests pass."})]


def test_done_without_summary_is_refused(plugin, fake):
    out = call(plugin, {"kind": "done", "summary": "  "})
    assert not out["ok"] and "summary" in out["error"] and fake.declared == []


def test_wait_declares_named_items(plugin, fake):
    out = call(plugin, {"kind": "wait", "on": [{"kind": "card", "id": "t_abc12345", "label": "deploy", "eta": 1793300000},
                                              {"kind": "capacity", "id": "hub", "label": "hub slot"}]})
    assert out["ok"]
    sid, outcome = fake.declared[0]
    assert outcome == {"kind": "waiting", "on": [
        {"kind": "card", "id": "t_abc12345", "label": "deploy", "eta": 1793300000.0},
        {"kind": "capacity", "id": "hub", "label": "hub slot"}]}


def test_wait_rejects_unknown_kind(plugin, fake):
    out = call(plugin, {"kind": "wait", "on": [{"kind": "vibes", "id": "x"}]})
    assert not out["ok"] and fake.declared == []


def test_decide_writes_steps_with_options_then_declares(plugin, fake, home):
    choices = [{"label": "Ship now", "value": "now"}, {"label": "Wait for review", "value": "wait"}]
    out = call(plugin, {"kind": "decide", "items": [{"question": "Ship build 412 to prod?", "choices": choices,
                                                    "card_id": "t_00000001"}]})
    assert out["ok"] and out["declared"] and len(out["steps"]) == 1
    ledger = json.loads((home / "user-steps" / "S1.json").read_text())
    step = ledger["steps"][0]
    assert step["id"] == out["steps"][0] and step["status"] == "open" and step["session"] == "S1"
    assert step["kind"] == "choose" and step["options"] == ["Ship now", "Wait for review"]
    assert step["choices"] == choices
    sid, outcome = fake.declared[0]
    assert outcome == {"kind": "decision", "items": [{"id": step["id"], "question": "Ship build 412 to prod?",
                                                      "choices": choices, "state": "open", "card_id": "t_00000001"}]}


def test_decide_needs_two_choices_and_writes_nothing(plugin, fake, home):
    out = call(plugin, {"kind": "decide", "items": [{"question": "OK?", "choices": [{"label": "Yes", "value": "y"}]}]})
    assert not out["ok"] and "2 choices" in out["error"]
    assert fake.declared == [] and not (home / "user-steps" / "S1.json").exists()


def test_decide_validates_every_item_before_writing(plugin, fake, home):
    good = {"question": "A or B?", "choices": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]}
    bad = {"question": "Only one?", "choices": [{"label": "Yes", "value": "y"}]}
    out = call(plugin, {"kind": "decide", "items": [good, bad]})
    assert not out["ok"] and "items[1]" in out["error"]
    assert fake.declared == [] and not (home / "user-steps" / "S1.json").exists()


def test_decide_failed_write_or_rejection_keeps_nothing(plugin, fake, home, monkeypatch):
    us = plugin._user_steps()
    keep = us.add_step("S1", "Pre-existing ask?", ["x", "y"])
    items = [{"question": f"Q{i}?", "choices": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]}
             for i in range(3)]
    real_add, n = us.add_step, {"i": 0}

    def flaky(*a, **kw):
        n["i"] += 1
        if n["i"] == 2:
            raise OSError("disk full")
        return real_add(*a, **kw)
    monkeypatch.setattr(us, "add_step", flaky)
    out = call(plugin, {"kind": "decide", "items": items})
    assert not out["ok"] and "nothing written" in out["error"]
    assert [s["id"] for s in us.open_steps("S1")] == [keep["id"]] and fake.declared == []

    monkeypatch.setattr(us, "add_step", real_add)
    monkeypatch.setattr(fake, "declare_outcome", lambda sid, o: (_ for _ in ()).throw(ValueError("bad item")))
    out = call(plugin, {"kind": "decide", "items": items + [{"question": "Pre-existing ask?",
                                                              "choices": [{"label": "x", "value": "x"},
                                                                          {"label": "y", "value": "y"}]}]})
    assert not out["ok"] and "nothing kept" in out["error"]
    assert [s["id"] for s in us.open_steps("S1")] == [keep["id"]]  # the pre-existing step survives


def test_bad_kind_is_refused(plugin, fake):
    assert not call(plugin, {"kind": "maybe"})["ok"] and fake.declared == []


def test_core_rejection_is_reported(plugin, fake, monkeypatch):
    def reject(sid, outcome):
        raise ValueError("summary too long")
    monkeypatch.setattr(fake, "declare_outcome", reject)
    out = call(plugin, {"kind": "done", "summary": "x"})
    assert not out["ok"] and "summary too long" in out["error"]


# ---- provider -----------------------------------------------------------------------------------------

def test_provider_returns_open_steps(plugin, fake):
    prov = fake.providers["user-steps"]
    assert prov("S1") is None
    call(plugin, {"kind": "decide", "items": [{"question": "Which region?", "choices": [
        {"label": "us-east", "value": "use1"}, {"label": "eu-west", "value": "euw1"}]}]})
    got = prov("S1")
    assert got["waiting_on"] == []
    [item] = got["decision_items"]
    assert item["question"] == "Which region?" and item["state"] == "open" and item["id"].startswith("us_")
    assert item["choices"] == [{"label": "us-east", "value": "use1"}, {"label": "eu-west", "value": "euw1"}]


# ---- judge --------------------------------------------------------------------------------------------

def test_judge_yes_no(plugin):
    plugin._llm = lambda s, u: "YES"
    assert plugin.judge("S1", "Deployed v2 and verified the health check.") is True
    plugin._llm = lambda s, u: "no."
    assert plugin.judge("S1", "Working on it, will report back.") is False


def test_judge_sees_head_and_tail_of_a_long_reply(plugin):
    # A long report leads with its answer and ends with sources; a tail-only cut hid the answer and the judge
    # called a finished research report "not a summary".
    seen = []
    plugin._llm = lambda s, u: seen.append(u) or "YES"
    head = "Yes, I found supporting results: six wins out of six reported comparisons. "
    tail = " Sources: [8] archived brief, [9] composite results."
    reply = head + ("x" * 30000) + tail
    assert plugin.judge("S1", reply) is True
    sent = seen[-1]
    assert sent.startswith(head) and sent.endswith(tail)
    assert "middle of the message omitted" in sent
    assert len(sent) <= plugin.JUDGE_INPUT_CHARS + 60
    short = "Deployed v3 and verified the health check passes."
    plugin.judge("S2", short)
    assert seen[-1] == short


def test_judge_only_interactive_sources(plugin):
    calls = []
    plugin._llm = lambda s, u: calls.append(u) or "YES"
    for src in ("slack", "cron", "kanban"):
        assert plugin.judge("S1", f"Finished the {src} job.", source=src) is None
    assert plugin.judge("K1", "Finished the slack job, row source.") is None  # no source kwarg: row says slack
    assert calls == []
    assert plugin.judge("S1", "Deployed and verified.", source="tui") is True
    assert plugin.judge("C1", "Deployed and verified, cli row.") is True
    assert len(calls) == 2


def test_judge_timeout_returns_none(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "JUDGE_BUDGET_S", 0.2)

    def slow(s, u):
        time.sleep(1.0)
        return "YES"
    plugin._llm = slow
    t0 = time.time()
    assert plugin.judge("S1", "Some reply text") is None
    assert time.time() - t0 < 0.9


def test_judge_failure_returns_none(plugin):
    def boom(s, u):
        raise RuntimeError("pool exhausted")
    plugin._llm = boom
    assert plugin.judge("S1", "Some reply text") is None
    plugin._llm = lambda s, u: "maybe"
    assert plugin.judge("S1", "Other reply text") is None


def test_judge_default_path_is_pool_fast_tier(plugin, monkeypatch):
    seen = {}

    class Pool:
        __file__ = str(ROOT.parent / "pool-llm" / "pool_llm.py")

        @staticmethod
        def complete(messages, **kw):
            seen.update(kw)
            return {"text": "YES"}
    monkeypatch.setattr(plugin, "_load_sibling", lambda key, rel: Pool)
    plugin._llm = plugin._pool_llm
    assert plugin.judge("S1", "Done: merged and deployed.") is True
    assert seen["tier"] == "fast"


# ---- pre_final_gate -----------------------------------------------------------------------------------

def gate(plugin, reply="I looked into it.", msgs=None, attempt=0, sid="S1", platform="desktop"):
    return plugin.pre_final_gate(session_id=sid, platform=platform, final_response=reply,
                                 turn_messages=msgs or [], attempt=attempt, hold_budget=2)


def test_gate_asks_once_then_passes_second_miss(plugin):
    first = gate(plugin)
    assert first and first["hold"] and plugin.TAG in first["nudge"]
    msgs = [{"role": "user", "content": first["nudge"], "_pre_final_gate_synthetic": True}]
    assert gate(plugin, msgs=msgs, attempt=1) is None
    # the in-memory backup signal alone also stops a loop
    gate(plugin)
    assert gate(plugin, attempt=1) is None


def test_gate_passes_when_tool_was_called(plugin):
    msgs = [{"role": "assistant", "tool_calls": [{"function": {"name": "session_outcome", "arguments": "{}"}}]}]
    assert gate(plugin, msgs=msgs) is None


def test_call_flag_is_per_turn(plugin, fake):
    out = json.loads(plugin.handle({"kind": "done", "summary": "ok"}, session_id="S1", tool_call_id="call_A"))
    assert out["declared"]
    # same turn: the call's id is in the turn (here only as the tool result row, no tool name)
    assert gate(plugin, msgs=[{"role": "tool", "tool_call_id": "call_A", "content": "{}"}]) is None
    # next turn: that call does not carry over; the gate asks again
    nxt = gate(plugin, msgs=[{"role": "user", "content": "and now?"}])
    assert nxt and nxt["hold"]
    # same call id in another chat does not count either
    assert gate(plugin, sid="T1", platform="tui", msgs=[{"role": "tool", "tool_call_id": "call_A"}])["hold"]


def test_gate_passes_summary_and_undecided_judge_uses_heuristic(plugin, fake):
    plugin._llm = lambda s, u: "YES"
    assert gate(plugin, reply="Merged #12 and deployed; health check green.") is None
    assert fake.derive_calls[-1] == {"sid": "S1", "ignore_live_turn": True,
                                     "pending_reply": "Merged #12 and deployed; health check green."}
    plugin._llm = lambda s, u: (_ for _ in ()).throw(RuntimeError("down"))
    assert gate(plugin, reply="Rebased, fixed both failing tests and pushed; CI is green.") is None
    assert gate(plugin, reply="Hmm?")["hold"]


def test_gate_fails_open_on_old_core_signature(plugin, fake, monkeypatch):
    monkeypatch.setattr(fake, "derive_outcome", lambda sid: {"kind": "unresolved", "reason": "no_summary"})
    assert gate(plugin) is None


def test_gate_passes_when_decision_or_wait_outranks(plugin, fake):
    fake.derived["S1"] = {"kind": "waiting", "on": [{"kind": "card", "id": "t_1"}]}
    assert gate(plugin) is None
    del fake.derived["S1"]
    call(plugin, {"kind": "decide", "items": [{"question": "A or B?", "choices": [
        {"label": "A", "value": "a"}, {"label": "B", "value": "b"}]}]})
    plugin._CALLED.clear()
    assert gate(plugin) is None  # open step -> decision


# ---- interactive only -----------------------------------------------------------------------------------

def test_non_interactive_source_gets_no_tool(plugin, monkeypatch):
    assert plugin.check_fn() is True
    for env in ({"HERMES_SESSION_SOURCE": "cron"}, {"HERMES_SESSION_SOURCE": "", "HERMES_SESSION_PLATFORM": "slack"},
                {"HERMES_KANBAN_TASK": "t_1"}):
        with monkeypatch.context() as m:
            for k, v in env.items():
                m.setenv(k, v)
            assert plugin.check_fn() is False
    for src in ("tui", "cli"):
        monkeypatch.setenv("HERMES_SESSION_SOURCE", src)
        assert plugin.check_fn() is True


def test_non_interactive_session_refused_and_not_gated(plugin, fake):
    for sid in ("K1", "R1"):
        out = call(plugin, {"kind": "done", "summary": "x"}, sid=sid)
        assert not out["ok"] and "only for" in out["error"]
        assert gate(plugin, sid=sid, platform="slack") is None
    assert fake.declared == []
    assert call(plugin, {"kind": "done", "summary": "x"}, sid="T1")["ok"]
    assert call(plugin, {"kind": "done", "summary": "x"}, sid="C1")["ok"]


def test_register_wires_tool_gate_provider_and_judge(plugin, fake):
    fake.providers.clear()
    fake.judges.clear()
    ctx = Ctx()
    plugin.register(ctx)
    assert ctx.tools["session_outcome"]["schema"]["name"] == "session_outcome"
    assert ctx.tools["session_outcome"]["check_fn"] is plugin.check_fn
    assert ctx.hooks["pre_final_gate"] is plugin.pre_final_gate
    assert plugin.pre_final_gate.needs_buffer is True
    assert set(fake.providers) == {"user-steps"} and fake.judges == [plugin.judge]


# ---- no core: everything is a no-op -------------------------------------------------------------------------

def test_no_core_is_a_noop(home, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_state_outcome", None)  # import raises ImportError
    mod = _load()
    mod._llm = lambda s, u: "NO"
    ctx = Ctx()
    mod.register(ctx)  # loads without error
    assert "session_outcome" in ctx.tools
    assert mod.check_fn() is False  # tool not offered
    assert gate(mod) is None
    out = json.loads(mod.handle({"kind": "done", "summary": "x"}, session_id="S1"))
    assert out["ok"] and out["declared"] is False

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
        self.declared, self.providers, self.judges, self.derived = [], {}, [], {}

    def declare_outcome(self, sid, outcome):
        if outcome.get("kind") not in ("done", "decision", "waiting", "running", "muted", "unresolved"):
            raise ValueError("bad kind")
        self.declared.append((sid, outcome))

    def derive_outcome(self, sid):
        return self.derived.get(sid, {"kind": "running", "source": "derived"})

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
    for k in ("HERMES_KANBAN_TASK", "HERMES_SESSION_OUTCOME_OFF", "HERMES_PLATFORM", "HERMES_SESSION_PLATFORM"):
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
    ctx = Ctx()
    plugin.register(ctx)
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
    call(plugin, {"kind": "done", "summary": "ok"})
    assert gate(plugin) is None  # handler flag


def test_gate_passes_summary_and_undecided_judge(plugin):
    plugin._llm = lambda s, u: "YES"
    assert gate(plugin, reply="Merged #12 and deployed; health check green.") is None
    plugin._llm = lambda s, u: (_ for _ in ()).throw(RuntimeError("down"))
    assert gate(plugin, reply="Something else entirely.") is None


def test_gate_passes_when_decision_or_wait_outranks(plugin, fake):
    fake.derived["S1"] = {"kind": "waiting", "on": [{"kind": "card", "id": "t_1"}]}
    assert gate(plugin) is None
    fake.derived["S1"] = {"kind": "running"}
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

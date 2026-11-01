"""A coach plugin reload in the middle of a nudged turn keeps that nudge's delivery and outcome records.

hermes-sync's mods-reload force-reloads the coach in every running gateway/serve whenever a coach file changes: the
plugin manager purges the plugin package and runs register() again, which used to build() a fresh AgentCoach and
fresh closure dicts. A nudge parked by the old build (pre_verify attempt 0) then met an empty pending map, turn
counter 0 and no outcome slot in the new build's continuation check and post_llm_call, so it got neither record
in-process (hub n_9ba5…, reload 3 s after the nudge; MacBook n_2083…, reload 5 min after), the post_llm_call logged a
false `reply_skipped pre_verify_not_called` at turn 0, and the signals sweep filed an "ignored nudge" agent defect at
15 min. These tests drive the real register() of two separately executed copies of __init__.py, the way a force
reload does. Synthetic strings only (public repo).
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
REPLY = "I patched the parser.\n\nWant me to run the full suite now?"  # synthetic: prefilter `confirm` candidate
REVISED = "I ran the full suite: 12 passed."
VERDICT = {"handoff": 0.0, "confirm": 0.95, "unverified": 0.0, "unwatched": 0.0, "report_only": 0.0,
           "evidence": "asks permission for a reversible step"}
DELIVERY = ("reply_nudge_delivered", "reply_nudge_undelivered", "reply_nudge_abandoned")
OUTCOME = ("reply_nudge_outcome", "nudge_outcome_lost", "nudge_outcome_subagent", "nudge_outcome_untracked",
           "nudge_outcome_missing")
LIVE_KEY = "_coach_live_state"

pytest.importorskip("hermes_cli.plugins")


class _Ctx:
    def __init__(self):
        self.hooks: dict = {}

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    for k, v in {"COACH_REPLY_RULES": "confirm", "COACH_SYSTEM_ONE": "off", "COACH_REPORT_ONLY_CONFIRM": "0",
                 "COACH_DIFF_REVIEW": "0"}.items():
        monkeypatch.setenv(k, v)
    import agent.auxiliary_client as aux

    def model(task=None, messages=None, timeout=None, **_):
        msg = types.SimpleNamespace(content=json.dumps(VERDICT))
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setattr(aux, "call_llm", model)
    # the handover lives in the process; each test starts from a process that never loaded the coach
    monkeypatch.delitem(sys.modules, LIVE_KEY, raising=False)
    log_path = tmp_path / "home" / "coach" / "log.jsonl"

    def events(name=None):
        rows = [json.loads(ln) for ln in log_path.read_text().splitlines()] if log_path.exists() else []
        return [r for r in rows if name is None or r.get("event") == name]
    return tmp_path, events


_N = [0]


def _register():
    """One plugin load: a fresh execution of __init__.py (a force reload purges the package and re-imports it)
    and its register(ctx). Returns {event: callback} as the host would dispatch them."""
    _N[0] += 1
    name = f"coach_init_reload_{_N[0]}"
    spec = importlib.util.spec_from_file_location(name, HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ctx = _Ctx()
    mod.register(ctx)
    return {k: v[-1] if k != "pre_final_gate" else v for k, v in ctx.hooks.items()}


def _records(events, nid):
    rows = [r for r in events() if r.get("nudge_id") == nid]
    return [r for r in rows if r["event"] in DELIVERY], [r for r in rows if r["event"] in OUTCOME]


def _nudge(hooks, sid):
    hooks["pre_llm_call"](session_id=sid, user_message="fix the parser", platform="cli")
    out = hooks["pre_verify"](session_id=sid, platform="cli", attempt=0, final_response=REPLY,
                              changed_paths=["parser.py"])
    assert out and out["action"] == "continue", out


def test_reload_before_the_continuation_check(env):
    """Hub-shaped: the reload lands between the nudge and its continuation's check, both in the same turn."""
    _tmp, events = env
    old = _register()
    _nudge(old, "s1")
    [n] = events("reply_nudge")
    new = _register()  # mods-reload: the host now dispatches to the new build
    assert new["pre_verify"](session_id="s1", platform="cli", attempt=1, final_response=REVISED,
                             changed_paths=["parser.py"]) is None
    new["post_llm_call"](session_id="s1", assistant_response=REVISED, platform="cli")
    d, o = _records(events, n["nudge_id"])
    assert [(r["event"], r.get("via")) for r in d] == [("reply_nudge_delivered", "continuation")], d
    assert [r["event"] for r in o] == ["reply_nudge_outcome"] and o[0]["resolved"] is True, o
    # the outcome judge still had the turn's ask: it is the continuation of the same turn, not a turn-0 stranger
    assert [r for r in events("reply_skipped") if r.get("session_id") == "s1"] == []
    # the next turn adds nothing for the closed nudge
    new["pre_llm_call"](session_id="s1", user_message="thanks", platform="cli")
    assert tuple(map(len, _records(events, n["nudge_id"]))) == (1, 1)


def test_reload_then_the_turn_ends_unchecked(env):
    """The turn ends in the new build without another reply check: closed at post_llm_call, not left to the 3 h
    sweep, and the turn is not reported as one the host never handed to pre_verify."""
    _tmp, events = env
    old = _register()
    _nudge(old, "s2")
    [n] = events("reply_nudge")
    new = _register()
    new["post_llm_call"](session_id="s2", assistant_response=REVISED, platform="cli")
    d, o = _records(events, n["nudge_id"])
    assert [(r["event"], r.get("reason")) for r in d] == [("reply_nudge_undelivered", "turn_ended")], d
    assert [(r["event"], r.get("reason")) for r in o] == [("nudge_outcome_lost", "turn_ended")], o
    assert [r for r in events("reply_skipped") if r.get("session_id") == "s2"] == []


def test_reload_logs_the_handover(env):
    """Audit trail: a re-register that adopts live sessions says how many nudges it carried over."""
    _tmp, events = env
    old = _register()
    _nudge(old, "s3")
    assert events("coach_state_adopted") == []  # the first load adopts nothing
    _register()
    [a] = events("coach_state_adopted")
    assert a["sessions"] >= 1 and a["open_nudges"] == 1, a


def test_handover_fills_keys_a_newer_coach_adds(env):
    """A reload that also changed agent_rules may expect per-session keys the old build never created: adopted
    sessions get them, so the new build's hooks run instead of failing open on a KeyError."""
    _tmp, events = env
    old = _register()
    _nudge(old, "s4")
    [n] = events("reply_nudge")
    homes = sys.modules[LIVE_KEY].homes
    [state] = homes.values()
    for k in ("turn_rich", "turn_log", "fired"):
        state["sessions"]["s4"].pop(k)  # an older AgentCoach shape
    new = _register()
    new["pre_verify"](session_id="s4", platform="cli", attempt=1, final_response=REVISED, changed_paths=["p.py"])
    new["post_llm_call"](session_id="s4", assistant_response=REVISED, platform="cli")
    d, o = _records(events, n["nudge_id"])
    assert [r["event"] for r in d] == ["reply_nudge_delivered"] and [r["event"] for r in o] == ["reply_nudge_outcome"]


def test_handover_is_per_home(env, monkeypatch):
    """Profiles in one process register their own coach (own HERMES_HOME, own store): a reload of one never hands
    its sessions to another."""
    tmp, events = env
    old = _register()
    _nudge(old, "s5")
    monkeypatch.setenv("HERMES_HOME", str(tmp / "other"))
    other = _register()
    other["post_llm_call"](session_id="s5", assistant_response=REVISED, platform="cli")
    [n] = events("reply_nudge")
    assert _records(events, n["nudge_id"]) == ([], [])  # the other home's build never saw the nudge
    assert len(sys.modules[LIVE_KEY].homes) == 2


def test_build_without_live_state_is_isolated(env):
    """Tests (and any caller of build() without register) keep a private state, as before."""
    tmp, events = env
    spec = importlib.util.spec_from_file_location("coach_init_reload_iso", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = mod._load("core").Store(root=tmp / "home" / "coach")
    c1, h1 = mod.build(store=store, checkout=None)
    c2, _h2 = mod.build(store=store, checkout=None)
    _nudge(h1, "s6")
    assert c1.sessions is not c2.sessions and "s6" not in c2.sessions


def test_handover_keeps_sessions_when_old_hook_adds_one(env):
    """Inject a live writer exactly during adoption's telemetry iteration."""
    _tmp, events = env
    old = _register()
    _nudge(old, "s7")
    [n] = events("reply_nudge")
    [state] = sys.modules[LIVE_KEY].homes.values()
    sessions = state["sessions"]

    class ConcurrentSession(dict):
        loop_reads = 0

        def get(self, key, default=None):
            if key == "loop":
                self.loop_reads += 1
                if self.loop_reads == 2:  # after migration, during telemetry enumeration
                    sessions.setdefault("concurrent", {})
            return super().get(key, default)

    sessions["s7"] = ConcurrentSession(sessions["s7"])
    new = _register()
    assert state["sessions"] is sessions
    new["pre_verify"](session_id="s7", platform="cli", attempt=1, final_response=REVISED)
    assert tuple(map(len, _records(events, n["nudge_id"]))) == (1, 1)


def test_handover_logging_failure_never_discards_shared_sessions(env, monkeypatch):
    _tmp, events = env
    old = _register()
    _nudge(old, "s8")
    [n] = events("reply_nudge")
    [state] = sys.modules[LIVE_KEY].homes.values()
    sessions = state["sessions"]
    core = sys.modules["_coach_core"]
    original = core.Store.log

    def log(self, row):
        if row.get("event") == "coach_state_adopted":
            raise OSError("synthetic telemetry failure")
        return original(self, row)

    monkeypatch.setattr(core.Store, "log", log)
    new = _register()
    assert state["sessions"] is sessions
    new["pre_verify"](session_id="s8", platform="cli", attempt=1, final_response=REVISED)
    assert tuple(map(len, _records(events, n["nudge_id"]))) == (1, 1)

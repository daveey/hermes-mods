"""Deterministic, delivery-scoped agent hint outcomes (no model or database)."""
import importlib.util
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coach_hint_test_plugin", HERE / "__init__.py")
assert spec and spec.loader
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


@pytest.fixture
def built(tmp_path, monkeypatch):
    for key in ("COACH_LESSONS", "COACH_SELF_HEAL", "COACH_REPLY_CHECK", "COACH_COMPUTE_ENVELOPE"):
        monkeypatch.setenv(key, "0")
    monkeypatch.setenv("COACH_REPLY_RULES", "")
    monkeypatch.setenv("COACH_BOARD_DBS", "")
    store = plugin._load("core").Store(tmp_path / "coach")
    live = {}
    coach, hooks = plugin.build(store=store, live=live)
    return coach, hooks, store, live


def rows(store, event="agent_hint_outcome"):
    return [r for r in store.read_log() if r.get("event") == event]


def fire(coach, rule="no_checkpoint", sid="s", key="test"):
    coach._fire(coach._s(sid), sid, rule, key, n=40)


def call(hooks, i, tool="read_file", args=None, result: object='{"success":true}', sid="s", order="sequential"):
    kw = dict(session_id=sid, tool_name=tool, args=args or {}, tool_call_id=str(i))
    hooks["pre_tool_call"](**kw)
    if order == "sequential":
        hooks["transform_tool_result"](**kw, result=result)
    # Core's _tool_result_observer_fields emits "ok", not "success".
    hooks["post_tool_call"](**kw, result=result, status="ok")
    if order == "concurrent":
        hooks["transform_tool_result"](**kw, result=result)


@pytest.mark.parametrize("order", ["sequential", "concurrent"])
def test_delivery_call_excluded_and_window_exactly_five(built, order):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0, order=order)
    for i in range(1, 5):
        call(hooks, i, order=order)
    assert rows(store) == []
    call(hooks, 5, order=order)
    [out] = rows(store)
    [hint] = rows(store, "agent_hint")
    assert out["hint_id"] == hint["hint_id"]
    assert out["rule"] == "no_checkpoint" and out["session_id"] == "s"
    assert out["heeded"] is False and out["evidence"]
    call(hooks, 6, order=order)
    assert len(rows(store)) == 1


@pytest.mark.parametrize("tool", ["kanban_continue", "kanban_comment", "kanban_heartbeat"])
@pytest.mark.parametrize("success", [True, False])
def test_checkpoint_requires_success(built, tool, success):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0)
    call(hooks, 1, tool, result=json.dumps({"success": success}))
    for i in range(2, 6):
        call(hooks, i)
    assert rows(store)[0]["heeded"] is success


def test_commentary_is_after_delivery_nonempty_and_session_local(built):
    coach, hooks, store, _ = built
    assert "on_interim_message" in hooks
    hooks["on_interim_message"](session_id="s", text="old progress")
    fire(coach)
    call(hooks, 0)
    hooks["on_interim_message"](session_id="s", text="  ")
    hooks["on_interim_message"](session_id="other", text="progress")
    assert rows(store) == []
    hooks["on_interim_message"](session_id="s", text="new progress")
    hooks["on_interim_message"](session_id="s", text="new progress")
    assert [r["heeded"] for r in rows(store)] == [True]


@pytest.mark.parametrize("polls,loop,expected", [(0, False, True), (2, False, True), (3, False, False), (0, True, False)])
def test_poll_window(built, polls, loop, expected):
    coach, hooks, store, _ = built
    fire(coach, "poll_loop")
    call(hooks, 0)
    for i in range(1, 6):
        tool, args = ("process_manage", {"action": "poll"}) if i <= polls else ("read_file", {})
        if loop and i == 1:
            tool, args = "terminal", {"command": "while true; do sleep 2; done"}
        call(hooks, i, tool, args)
    out = [r for r in rows(store) if r["rule"] == "poll_loop"]
    assert len(out) == 1 and out[0]["heeded"] is expected


@pytest.mark.parametrize("rule", ["poll_loop", "no_checkpoint"])
def test_early_final_resolves_and_new_turn_does_not_duplicate(built, rule):
    coach, hooks, store, _ = built
    fire(coach, rule)
    call(hooks, 0)
    coach.turn_end("s", "Verified progress.")
    coach.turn_end("s", "Verified progress.")
    coach.turn_start("s", "next")
    assert [r["heeded"] for r in rows(store)] == [True]


def test_unknown_and_undelivered_are_not_false_or_true(built):
    coach, hooks, store, _ = built
    fire(coach)
    coach.turn_end("s", "progress before delivery")
    assert rows(store) == []
    call(hooks, 0)
    call(hooks, 1)
    coach.turn_start("s", "next")
    for i in range(2, 8):
        call(hooks, i)
    assert rows(store) == []
    fire(coach, "poll_loop", key="unknown")
    call(hooks, 9)
    hooks["post_llm_call"](session_id="s", assistant_response="")
    assert rows(store) == []


def test_non_string_transform_does_not_consume_hint(built):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0, result={"success": True})
    for i in range(1, 7):
        hooks["post_tool_call"](session_id="s", tool_name="read_file", result={}, status="success")
    assert rows(store) == []
    assert coach.take("s")


def test_reload_legacy_and_unique_ids(built):
    coach, hooks, store, live = built
    coach._s("s")["pending"].append("legacy hint without id")
    call(hooks, 0)
    for i in range(1, 6):
        call(hooks, i)
    assert rows(store) == []
    fire(coach)
    fire(coach, "poll_loop")
    call(hooks, 6)
    new, hooks = plugin.build(store=store, live=live)
    for i in range(7, 12):
        call(hooks, i)
    outcomes = rows(store)
    assert len(outcomes) == 2
    assert len({r["hint_id"] for r in outcomes}) == 2
    assert new.sessions is coach.sessions


def test_inflight_parallel_calls_excluded_and_duplicate_posts_ignored(built):
    coach, hooks, store, _ = built
    pre = lambda i: hooks["pre_tool_call"](session_id="s", tool_name="read_file", args={}, tool_call_id=str(i))
    post = lambda i: hooks["post_tool_call"](session_id="s", tool_name="read_file", args={}, result='{}', status="success", tool_call_id=str(i))
    for i in range(3):
        pre(i)
    fire(coach)
    hooks["transform_tool_result"](session_id="s", tool_name="read_file", result='{}')
    for i in range(3):
        post(i)
    assert rows(store) == []
    with ThreadPoolExecutor(5) as pool:
        list(pool.map(pre, range(3, 8)))
        list(pool.map(post, [7, 6, 5, 4]))
    assert rows(store) == []
    post(3)
    post(3)
    assert [r["heeded"] for r in rows(store)] == [False]


def test_unmatched_post_invalidates_incomplete_window(built):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0)
    for i in range(1, 5):
        call(hooks, i)
    hooks["post_tool_call"](session_id="s", tool_name="read_file", tool_call_id="missing-pre",
                            result='{}', status="success")
    call(hooks, 5)
    assert rows(store) == []


@pytest.mark.parametrize("polls,expected", [(0, True), (2, True), (3, False)])
def test_early_poll_end_with_observed_repeats(built, polls, expected):
    coach, hooks, store, _ = built
    fire(coach, "poll_loop")
    call(hooks, 0)
    for i in range(1, polls + 1):
        call(hooks, i, "process_manage", {"action": "poll"})
    hooks["post_llm_call"](session_id="s", assistant_response="Job handed off.")
    assert [r["heeded"] for r in rows(store)] == [expected]


@pytest.mark.parametrize("status", ["cancelled", None])
def test_incomplete_status_never_resolves_as_false(built, status):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0)
    hooks["pre_tool_call"](session_id="s", tool_name="read_file", tool_call_id="unknown")
    hooks["post_tool_call"](session_id="s", tool_name="read_file", tool_call_id="unknown", status=status)
    for i in range(1, 6):
        call(hooks, i)
    assert rows(store) == []


def test_missing_ids_abstain_and_background_loop_is_not_foreground(built):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0)
    hooks["pre_tool_call"](session_id="s", tool_name="read_file")
    for i in range(1, 6):
        call(hooks, i)
    assert rows(store) == []
    fire(coach, "poll_loop")
    call(hooks, 6)
    call(hooks, 7, "terminal", {"command": "while true; do sleep 2; done", "background": True})
    for i in range(8, 12):
        call(hooks, i)
    assert [r["heeded"] for r in rows(store)] == [True]


def test_checkpoint_delivering_hint_does_not_heed_it(built):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0, "kanban_comment")
    for i in range(1, 6):
        call(hooks, i)
    assert [r["heeded"] for r in rows(store)] == [False]


def test_asynchronous_commentary_no_duplicate_outcomes(built):
    coach, hooks, store, _ = built
    fire(coach)
    call(hooks, 0)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: hooks["on_interim_message"](session_id="s", text="Progress."), range(32)))
    assert [r["heeded"] for r in rows(store)] == [True]


@pytest.mark.parametrize("text", ["", "This turn did not complete. The backend stopped."])
def test_failed_or_empty_turn_does_not_mean_heeded(built, text):
    coach, hooks, store, _ = built
    fire(coach, "poll_loop")
    fire(coach)
    call(hooks, 0)
    hooks["post_llm_call"](session_id="s", assistant_response=text)
    assert rows(store) == []


def test_next_turn_context_delivers_previously_pending_hint(built):
    coach, hooks, store, _ = built
    fire(coach)
    assert coach.turn_start("s", "next request")
    for i in range(5):
        call(hooks, i)
    assert [r["heeded"] for r in rows(store)] == [False]


def test_reload_preserves_lock_and_inflight_window(built):
    coach, old, store, live = built
    fire(coach)
    call(old, 0)
    old["pre_tool_call"](session_id="s", tool_name="read_file", tool_call_id="inflight")
    spec2 = importlib.util.spec_from_file_location("coach_hint_reload", HERE / "__init__.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    new, hooks = mod.build(store=store, live=live)
    assert new.hint_lock is coach.hint_lock
    hooks["post_tool_call"](session_id="s", tool_name="read_file", tool_call_id="inflight", result='{}', status="success")
    for i in range(1, 5):
        call(hooks, i)
    old["on_interim_message"](session_id="s", text="late old callback")
    assert [r["heeded"] for r in rows(store)] == [False]


def test_real_no_checkpoint_trigger_has_id_and_delivery(built):
    coach, hooks, store, _ = built
    for i in range(40):
        call(hooks, i)
    [hint] = [r for r in rows(store, "agent_hint") if r["rule"] == "no_checkpoint"]
    assert hint["hint_id"]
    call(hooks, 40)  # sequential: this result, not the triggering call, delivers it
    for i in range(41, 46):
        call(hooks, i)
    assert rows(store)[0]["hint_id"] == hint["hint_id"]

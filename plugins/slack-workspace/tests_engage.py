"""Tests for slack-workspace engagements (engage.py).
Run: uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/slack-workspace"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load("slack_workspace_core_t", "core.py")
engage = _load("slack_workspace_engage_t", "engage.py")

BOT_U, BOT_B = "UBOTSELF", "BBOTSELF"


@pytest.fixture
def eng(tmp_path):
    store = core.Store(tmp_path)
    return engage.Engagements(store)


def msg(ch, ts, text="hi", user="UBOB", thread="", bot_id=""):
    ev = {"type": "message", "channel": ch, "ts": ts, "text": text, "user": user}
    if thread:
        ev["thread_ts"] = thread
    if bot_id:
        ev["bot_id"] = bot_id
    return ev


# ---- CRUD ---------------------------------------------------------------------------------------
def test_create_get_list_and_end(eng):
    e = eng.create("C1", "follow the research", name="proj", owner="@session:default@this-device/s1",
                   kanban_ids=["t_1"], budget="$20 GPU")
    assert e["state"] == "active" and e["channel"] == "C1" and e["kanban_ids"] == ["t_1"]
    assert eng.get(e["id"])["brief"] == "follow the research"
    assert [x["id"] for x in eng.list()] == [e["id"]]
    eng.set_state(e["id"], "ended")
    assert eng.get(e["id"])["state"] == "ended"
    assert eng.list(states=("active",)) == []


def test_one_active_engagement_per_scope(eng):
    eng.create("C1", "a")
    with pytest.raises(ValueError):
        eng.create("C1", "b")
    eng.create("C1", "thread only", thread="111.1")  # a thread scope is distinct


def test_active_for_prefers_thread_scope_and_respects_expiry_and_state(eng):
    whole = eng.create("C1", "channel")
    th = eng.create("C1", "thread", thread="5.0")
    assert eng.active_for("C1", "5.0")["id"] == th["id"]
    assert eng.active_for("C1", "6.0")["id"] == whole["id"]
    assert eng.active_for("C2", "6.0") is None
    eng.set_state(whole["id"], "paused")
    assert eng.active_for("C1", "6.0") is None
    gone = eng.create("C3", "x", until=time.time() - 1)
    assert eng.active_for("C3", "1.0") is None
    assert eng.get(gone["id"])["state"] == "active"


def test_update_fields(eng):
    e = eng.create("C1", "a")
    eng.update(e["id"], follower_session="sess-1", last_handled_ts="10.5", brief="b")
    got = eng.get(e["id"])
    assert (got["follower_session"], got["last_handled_ts"], got["brief"]) == ("sess-1", "10.5", "b")
    with pytest.raises(ValueError):
        eng.update(e["id"], nope=1)


# ---- routing ------------------------------------------------------------------------------------
def route(eng, ev):
    return engage.route(ev, eng, bot_user_id=BOT_U, bot_id=BOT_B)


def test_unengaged_channel_is_ambient(eng):
    r = route(eng, msg("C9", "1.0", f"<@{BOT_U}> hi"))
    assert r.decision == "ambient" and not r.trigger


def test_human_message_in_engaged_channel_goes_to_follower(eng):
    e = eng.create("C1", "a")
    r = route(eng, msg("C1", "1.0", "anyone know?"))
    assert r.decision == "engaged" and r.trigger and r.engagement["id"] == e["id"]


def test_mention_in_engaged_channel_goes_to_follower_only_once(eng):
    eng.create("C1", "a")
    text = f"<@{BOT_U}> can you check"
    m = route(eng, msg("C1", "2.0", text))
    am = route(eng, {"type": "app_mention", "channel": "C1", "ts": "2.0", "text": text, "user": "UBOB"})
    assert m.decision == am.decision == "engaged"  # ambient bot never sees either
    assert [m.trigger, am.trigger] == [True, False]  # one follower trigger for the one message


def test_own_edits_and_unaddressed_bot_messages_are_swallowed_without_trigger(eng):
    eng.create("C1", "a")
    own = route(eng, msg("C1", "3.0", "my reply", user=BOT_U, bot_id=BOT_B))
    edit = route(eng, {"type": "message", "subtype": "message_changed", "channel": "C1",
                       "message": {"ts": "1.0", "text": "x"}})
    other_bot = route(eng, msg("C1", "4.0", "tool notice", user="UOTHERBOT", bot_id="BOTHER"))
    asked_bot = route(eng, msg("C1", "5.0", f"<@{BOT_U}> what is X?", user="UOTHERBOT", bot_id="BOTHER"))
    assert [own.decision, edit.decision, other_bot.decision] == ["engaged"] * 3
    assert [own.trigger, edit.trigger, other_bot.trigger] == [False, False, False]
    assert asked_bot.trigger


def test_thread_engagement_leaves_rest_of_channel_ambient(eng):
    eng.create("C1", "a", thread="7.0")
    assert route(eng, msg("C1", "8.0", "in thread", thread="7.0")).trigger
    assert route(eng, msg("C1", "7.0", "the parent")).trigger
    assert route(eng, msg("C1", "9.0", "elsewhere")).decision == "ambient"


def test_ended_engagement_stops_routing(eng):
    e = eng.create("C1", "a")
    eng.set_state(e["id"], "ended")
    assert route(eng, msg("C1", "1.0", f"<@{BOT_U}> hi")).decision == "ambient"


# ---- unhandled batch ----------------------------------------------------------------------------
def test_unhandled_returns_trigger_messages_after_watermark(eng):
    e = eng.create("C1", "a", last_handled_ts="1.0")
    for ev in (msg("C1", "0.5", "old"), msg("C1", "2.0", "new"),
               msg("C1", "3.0", "mine", user=BOT_U, bot_id=BOT_B),
               msg("C1", "4.0", "noise", user="UF", bot_id="BF"),
               msg("C2", "5.0", "other channel")):
        eng.store.record_event(ev)
    batch = eng.unhandled(e["id"], bot_user_id=BOT_U)
    assert [m["ts"] for m in batch["trigger"]] == ["2.0"]
    assert batch["newest"] == "4.0"
    eng.mark_handled(e["id"], batch["newest"])
    assert eng.unhandled(e["id"], bot_user_id=BOT_U)["trigger"] == []


# ---- exactly one reply per message --------------------------------------------------------------
def test_reply_claim_is_once_per_message_and_releasable(eng):
    assert eng.claim_reply("C1", "2.0")
    assert not eng.claim_reply("C1", "2.0")
    eng.release_reply("C1", "2.0")
    assert eng.claim_reply("C1", "2.0")


# ---- bot-to-bot cap -----------------------------------------------------------------------------
def test_bot_loop_cap_three_turns_then_blocked_until_a_human_speaks(eng):
    eng.create("C1", "a")
    for i in range(3):
        assert eng.bot_turn_allowed("C1", "7.0")
        eng.bump_bot_turns("C1", "7.0")
    assert not eng.bot_turn_allowed("C1", "7.0")
    assert eng.bot_turn_allowed("C1", "8.0")  # per thread
    route(eng, msg("C1", "9.0", "human again", thread="7.0"))
    assert eng.bot_turn_allowed("C1", "7.0")


# ---- inbox key ----------------------------------------------------------------------------------
def test_inbox_key_is_stable_per_channel_thread_topic():
    k1 = engage.inbox_key("C1", "7.0", "Pricing question!")
    k2 = engage.inbox_key("C1", "7.0", "  pricing   QUESTION ")
    assert k1 == k2 == "slack:C1:7.0:pricing-question"
    assert engage.inbox_key("C1", "", "x") == "slack:C1:-:x"
    assert engage.inbox_key("C1", "7.0", "other") != k1


# ---- MBP request queue --------------------------------------------------------------------------
def test_mbp_queue_retries_until_delivered(eng):
    e = eng.create("C1", "a")
    q = eng.queue_mbp(e["id"], "@session:default@this-device/s1", "run X", "C1", "7.0")
    assert [x["id"] for x in eng.due_mbp(now=time.time())] == [q["id"]]
    eng.mbp_attempt(q["id"], ok=False, error="desktop offline", retry_in=60)
    assert eng.due_mbp(now=time.time()) == []
    assert [x["id"] for x in eng.due_mbp(now=time.time() + 61)] == [q["id"]]
    eng.mbp_attempt(q["id"], ok=True)
    assert eng.due_mbp(now=time.time() + 1e6) == []
    assert eng.mbp_get(q["id"])["state"] == "delivered"

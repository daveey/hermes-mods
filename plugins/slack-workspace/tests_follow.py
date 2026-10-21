"""Tests for the follower side (follow.py): posting invariants, escalation, runner turns, ops.
Run: uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/slack-workspace"""

import importlib.util
import json
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


core = _load("slack_workspace_core", "core.py")
engage = _load("slack_workspace_engage", "engage.py")
follow = _load("slack_workspace_follow_t", "follow.py")

BOTSELF = "UBOTSELF"


class FakeAPI:
    def __init__(self):
        self.posts, self.n = [], 100
        self.reactions = []

    def __call__(self, method, params=None):
        params = params or {}
        if method == "chat.postMessage":
            self.n += 1
            ts = f"{self.n}.0"
            self.posts.append(dict(params, ts=ts))
            return {"ok": True, "ts": ts, "channel": params["channel"]}
        if method == "conversations.replies":
            return {"ok": True, "messages": [{"ts": p["ts"]} for p in self.posts]}
        if method in ("reactions.add", "reactions.remove"):
            self.reactions.append((method.split(".")[1], params["name"], params["timestamp"]))
            return {"ok": True}
        raise AssertionError(method)


@pytest.fixture
def env(tmp_path):
    store = core.Store(tmp_path)
    engs = engage.Engagements(store)
    api = FakeAPI()
    poster = follow.Poster(engs, api, lambda: BOTSELF)
    return store, engs, api, poster


def rec(store, ch, ts, text="q?", user="UBOB", thread="", bot_id=""):
    ev = {"type": "message", "channel": ch, "ts": ts, "text": text, "user": user}
    if thread:
        ev["thread_ts"] = thread
    if bot_id:
        ev["bot_id"] = bot_id
    store.record_event(ev)


# ---- slack_post invariants ----------------------------------------------------------------------
def test_post_refused_outside_an_active_engagement(env):
    _, engs, api, poster = env
    assert poster.post("C1", "hi")["error"].startswith("not_engaged")
    e = engs.create("C1", "a")
    engs.set_state(e["id"], "ended")
    assert poster.post("C1", "hi")["error"].startswith("not_engaged")
    assert api.posts == []


def test_exactly_one_reply_per_message(env):
    store, engs, api, poster = env
    engs.create("C1", "a")
    rec(store, "C1", "5.0")
    first = poster.post("C1", "answer", thread_ts="5.0", reply_to="5.0")
    second = poster.post("C1", "answer again", thread_ts="5.0", reply_to="5.0")
    assert first["ok"] and first["verified"]
    assert second["error"].startswith("already_replied") and second["posted_ts"] == first["ts"]
    assert len(api.posts) == 1 and api.posts[0]["thread_ts"] == "5.0"


def test_bot_to_bot_cap_refuses_fourth_reply_until_a_human_speaks(env):
    store, engs, api, poster = env
    engs.create("C1", "a")
    for i in range(4):
        rec(store, "C1", f"{10 + i}.0", f"<@{BOTSELF}> q{i}", user="UOTHERBOT", thread="9.0", bot_id="BF")
    for i in range(3):
        assert poster.post("C1", f"a{i}", thread_ts="9.0", reply_to=f"{10 + i}.0")["ok"]
    capped = poster.post("C1", "a3", thread_ts="9.0", reply_to="13.0")
    assert capped["error"].startswith("bot_loop_cap") and "slack_escalate" in capped["error"]
    assert len(api.posts) == 3
    # the refused message is not burned: after a human speaks it can still be answered
    engage.route({"type": "message", "channel": "C1", "ts": "14.0", "thread_ts": "9.0", "text": "ok",
                  "user": "UBOB"}, engs, bot_user_id=BOTSELF)
    assert poster.post("C1", "a3", thread_ts="9.0", reply_to="13.0")["ok"]


# ---- escalation ---------------------------------------------------------------------------------
def test_escalate_posts_one_inbox_card_per_topic_and_nothing_in_channel(env):
    store, engs, api, poster = env
    engs.create("C1", "research only", name="proj")
    rec(store, "C1", "20.0", "can David join our board?")
    cards = {}

    def inbox_post(title, body, accept_prompt, key=None, **_):
        updated = key in cards
        cards[key] = {"title": title, "body": body, "accept_prompt": accept_prompt,
                      "n": cards.get(key, {}).get("n", 0) + 1}
        return {"session_id": "s-" + key, "updated": updated}

    r1 = follow.escalate(engs, inbox_post, channel="C1", thread_ts="20.0", reply_to="20.0",
                         topic="Board seat", title="Board seat ask", body="who/what", reply_text="Let me check with David.")
    r2 = follow.escalate(engs, inbox_post, channel="C1", thread_ts="20.0", reply_to="21.0",
                         topic="board seat", title="Board seat ask (follow-up)", body="again", reply_text="x")
    assert r1["ok"] and r2["ok"] and r2["updated"] and r1["key"] == r2["key"] == "slack:C1:20.0:board-seat"
    assert len(cards) == 1 and cards[r1["key"]]["n"] == 2
    prompt = cards[r1["key"]]["accept_prompt"]
    assert "slack_post" in prompt and "thread_ts='20.0'" in prompt and "reply_to='21.0'" in prompt
    assert api.posts == []


def test_escalations_are_remembered_per_channel_for_topic_reuse(env):
    store, engs, api, poster = env
    engs.create("C1", "a")
    follow.escalate(engs, lambda **kw: {"session_id": "s1", "updated": False}, channel="C1", thread_ts="20.0",
                    topic="Board seat", title="Board seat ask", body="b", reply_text="x")
    follow.escalate(engs, lambda **kw: {"session_id": "s2", "updated": False}, channel="C2", thread_ts="1.0",
                    topic="other", title="t", body="b", reply_text="x")
    got = engs.escalations("C1")
    assert [(g["key"], g["topic"], g["title"]) for g in got] == [("slack:C1:20.0:board-seat", "Board seat", "Board seat ask")]
    e = engs.get(engs.list()[0]["id"])
    prompt = follow.batch_prompt(e, {"trigger": [], "all": []}, [], str, "U", engs.escalations("C1"))
    assert "slack:C1:20.0:board-seat" in prompt and "Board seat" in prompt


# ---- runner -------------------------------------------------------------------------------------
def make_runner(store, engs, spawned):
    def spawn(e, prompt):
        spawned.append((e["id"], prompt))
        return 0, "PROBE\n\nsession_id: 20261019_000000_abcdef\n"
    return follow.Runner(store, engs, token=lambda: "x", bot_user_id=lambda: BOTSELF, name=lambda u: u.lower(),
                         root_home=store.root, spawn=spawn)


def test_runner_kickoff_then_one_turn_per_batch_and_watermark(env):
    store, engs, _, _ = env
    e = engs.create("C1", "follow X", name="proj", owner="@session:default@this-device/s1", private=True,
                    last_handled_ts="1.0")
    spawned = []
    r = make_runner(store, engs, spawned)
    assert r.run_once(e["id"])
    assert "kickoff" in spawned[0][1] and "follow X" in spawned[0][1] and "@session:default@this-device/s1" in spawned[0][1]
    assert engs.get(e["id"])["follower_session"] == "20261019_000000_abcdef"
    assert not r.run_once(e["id"])  # nothing new
    rec(store, "C1", "2.0", "first")
    rec(store, "C1", "3.0", "second")
    assert r.run_once(e["id"])
    assert "ts=2.0" in spawned[-1][1] and "ts=3.0" in spawned[-1][1]
    assert engs.get(e["id"])["last_handled_ts"] == "3.0"
    assert not r.run_once(e["id"])
    assert len(spawned) == 2


def test_runner_failed_turn_does_not_spin_and_keeps_work_for_the_next_tick(env):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True, last_handled_ts="1.0")
    calls = []

    def bad(e_, prompt):
        calls.append(prompt)
        return 1, "Error: boom"
    r = follow.Runner(store, engs, token=lambda: "x", bot_user_id=lambda: BOTSELF, name=str,
                      root_home=store.root, spawn=bad)
    assert not r.run_once(e["id"])  # kickoff failed: stop, the tick retries
    assert engs.get(e["id"])["kickoff"] == 1
    engs.update(e["id"], kickoff=0)
    rec(store, "C1", "2.0", "hello")
    assert not r.run_once(e["id"])
    assert engs.get(e["id"])["last_handled_ts"] == "1.0"  # not marked handled
    assert len(calls) == 2


def test_spawn_env_points_hermes_home_at_the_gateway_root(env, monkeypatch, tmp_path):
    store, engs, _, _ = env
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])

        class R:
            returncode, stdout, stderr = 0, "session_id: s1\n", ""
        return R()
    monkeypatch.setattr(follow.subprocess, "run", fake_run)
    monkeypatch.setenv("HERMES_HOME", "/somewhere/profiles/bot")
    r = follow.Runner(store, engs, token=lambda: "x", bot_user_id=lambda: BOTSELF, name=str, root_home=tmp_path)
    e = engs.create("C1", "x", private=True)
    r._spawn(e, "hi")
    assert seen["env"]["HERMES_HOME"] == str(tmp_path)
    assert seen["cmd"][seen["cmd"].index("-p") + 1] == "slack-follow"


def test_runner_skips_ended_engagements(env):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    engs.update(e["id"], kickoff=0)
    engs.set_state(e["id"], "ended")
    rec(store, "C1", f"{time.time() + 5:.6f}", "hello?")
    spawned = []
    assert not make_runner(store, engs, spawned).run_once(e["id"])
    assert spawned == []


def test_tick_ends_expired_and_quiet_engagements(env):
    store, engs, _, _ = env
    a = engs.create("C1", "x", until=time.time() - 1, private=True)
    b = engs.create("C2", "y", private=True)
    engs.update(b["id"], kickoff=0, last_activity=time.time() - 8 * 86400)
    make_runner(store, engs, []).tick()
    assert engs.get(a["id"])["state"] == engs.get(b["id"])["state"] == "ended"
def test_test_engagement_escalations_stay_out_of_the_inbox_unless_toggled(env):
    store, engs, api, poster = env
    real = engs.create("C1", "real")
    test = follow.engagement_op(engs, "create", {"channel": "C2", "brief": "t", "test": True})["engagement"]
    assert test["test"] and not follow.engagement_op(engs, "get", {"id": real["id"]})["engagement"]["test"]
    sent = []
    post = lambda **kw: sent.append(kw) or {"session_id": "s", "updated": False}  # noqa: E731
    kw = dict(topic="Money", title="t", body="b", reply_text="r")
    r = follow.escalate(engs, post, channel="C2", thread_ts="5.0", **kw)
    assert r["ok"] and r["suppressed"] and sent == [] and api.posts == []
    assert engs.escalations("C2")[0]["suppressed"]["title"] == "t"  # recorded for verification
    assert follow.escalate(engs, post, channel="C1", thread_ts="5.0", **kw)["ok"] and len(sent) == 1
    assert follow.engagement_op(engs, "test_inbox", {}) == {"ok": True, "test_inbox": False}
    assert follow.engagement_op(engs, "test_inbox", {"enabled": True})["test_inbox"]
    r = follow.escalate(engs, post, channel="C2", thread_ts="5.0", **kw)
    assert not r.get("suppressed") and len(sent) == 2
    follow.engagement_op(engs, "test_inbox", {"enabled": False})
    follow.engagement_op(engs, "update", {"id": test["id"], "test": False})
    assert not follow.escalate(engs, post, channel="C2", thread_ts="6.0", **kw).get("suppressed") and len(sent) == 3


# ---- typed cards read back ------------------------------------------------------------------------
def test_escalate_posts_a_typed_reply_card_with_the_draft_as_a_field(env):
    store, engs, api, poster = env
    engs.create("C1", "research only", name="proj")
    sent = []
    follow.escalate(engs, lambda **kw: sent.append(kw) or {"session_id": "s"}, channel="C1", thread_ts="20.0",
                    reply_to="21.0", topic="Board seat", title="t", body="b", reply_text="Let me check.")
    follow.escalate(engs, lambda **kw: sent.append(kw) or {"session_id": "s"}, channel="C1", thread_ts="20.0",
                    topic="Other", title="t", body="b", action="Schedule the call")
    assert sent[0]["type"] == "slack-reply"
    assert sent[0]["fields"] == {"channel": "C1", "thread": "20.0", "reply_to": "21.0", "reply": "Let me check."}
    assert "slack_post" in sent[0]["accept_prompt"]  # the Post reply button still sends the bot-cap-exempt Do-it text
    assert sent[1]["type"] == "slack-action" and sent[1]["fields"] == {}


def test_escalate_does_not_reraise_a_topic_the_owner_ignored(env):
    store, engs, api, poster = env
    engs.create("C1", "x", name="proj")
    sent = []
    post = lambda **kw: sent.append(kw) or {"session_id": "s"}  # noqa: E731
    kw = dict(channel="C1", thread_ts="20.0", topic="Board seat", title="t", body="b", reply_text="r")
    key = "slack:C1:20.0:board-seat"
    for o in (None, {"state": "open", "outcome": None}, {"state": "archived", "outcome": "archived"},
              {"state": "resolved", "outcome": "post_edited"}):
        assert not follow.escalate(engs, post, inbox_outcome=lambda k, o=o: o, **kw).get("ignored")
    assert len(sent) == 4
    r = follow.escalate(engs, post, inbox_outcome=lambda k: {"state": "resolved", "outcome": "ignore"}, **kw)
    assert r["ok"] and r["ignored"] and r["key"] == key and len(sent) == 4

    def boom(k):
        raise RuntimeError("inbox down")
    assert not follow.escalate(engs, post, inbox_outcome=boom, **kw).get("ignored")  # unreadable = undecided


def test_follower_prompt_shows_the_owners_decision_on_an_earlier_card(env):
    store, engs, api, poster = env
    e = engs.create("C1", "x", name="proj", private=True)
    follow.escalate(engs, lambda **kw: {"session_id": "s"}, channel="C1", thread_ts="20.0", topic="Board seat",
                    title="Board seat ask", body="b", reply_text="r")
    outcomes = {"slack:C1:20.0:board-seat": {"state": "resolved", "outcome": "ignore", "at": 5}}
    r = follow.Runner(store, engs, token=lambda: "x", bot_user_id=lambda: BOTSELF, name=str, root_home=store.root,
                      inbox_outcome=lambda k, profile=None: outcomes.get(k))
    prompt = follow.batch_prompt(engs.get(e["id"]), {"trigger": [], "all": []}, [], str, "U", r._escalations(e))
    assert "owner pressed ignore" in prompt


def _quiet_engagement(engs, ago_days=6.5):
    e = engs.create("C1", "x", private=True)
    engs.update(e["id"], kickoff=0, last_activity=time.time() - ago_days * 86400, warned_quiet=time.time() - 60)
    return e["id"]


def _runner_with(store, engs, outcome):
    return follow.Runner(store, engs, token=lambda: "x", bot_user_id=lambda: BOTSELF, name=str,
                         root_home=store.root, inbox_outcome=lambda k, profile=None: outcome)


def test_quiet_end_card_keep_following_restarts_the_quiet_clock(env):
    store, engs, _, _ = env
    eid = _quiet_engagement(engs)
    _runner_with(store, engs, {"state": "resolved", "outcome": "keep", "at": time.time()}).tick()
    e = engs.get(eid)
    assert e["state"] == "active" and not e["warned_quiet"] and time.time() - e["last_activity"] < 5


def test_quiet_end_card_let_it_end_ends_now(env):
    store, engs, _, _ = env
    eid = _quiet_engagement(engs)
    _runner_with(store, engs, {"state": "resolved", "outcome": "let_end", "at": time.time()}).tick()
    assert engs.get(eid)["state"] == "ended"


def test_quiet_end_ignores_open_archived_and_an_older_cards_decision(env):
    store, engs, _, _ = env
    for o in ({"state": "open", "outcome": None}, {"state": "archived", "outcome": "archived"},
              {"state": "resolved", "outcome": "let_end", "at": time.time() - 3600}, None):
        eid = _quiet_engagement(engs)
        _runner_with(store, engs, o).tick()
        assert engs.get(eid)["state"] == "active", o
        engs.set_state(eid, "ended")


def test_keep_following_beats_the_auto_end_when_the_tick_is_late(env):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    engs.update(e["id"], kickoff=0, last_activity=time.time() - 8 * 86400, warned_quiet=time.time() - 2 * 86400)
    _runner_with(store, engs, {"state": "resolved", "outcome": "keep", "at": time.time() - 3600}).tick()
    assert engs.get(e["id"])["state"] == "active"


def test_quiet_card_is_posted_typed_once(env):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    engs.update(e["id"], kickoff=0, last_activity=time.time() - 6.5 * 86400)
    sent = []
    r = follow.Runner(store, engs, token=lambda: "x", bot_user_id=lambda: BOTSELF, name=str, root_home=store.root,
                      inbox_post=lambda **kw: sent.append(kw), inbox_outcome=lambda k, profile=None: None)
    r.tick(); r.tick()
    assert len(sent) == 1 and sent[0]["type"] == "slack-quiet-end" and sent[0]["fields"] == {"engagement": e["id"]}


# ---- ops ----------------------------------------------------------------------------------------
def test_engagement_ops_roundtrip(env):
    _, engs, _, _ = env
    c = follow.engagement_op(engs, "create", {"channel": "C1", "brief": "b", "until": "2d", "kanban_ids": ["t_1"]})
    eid = c["engagement"]["id"]
    assert c["ok"] and c["engagement"]["expires"] > time.time() + 86400
    assert [x["id"] for x in follow.engagement_op(engs, "list", {})["engagements"]] == [eid]
    assert follow.engagement_op(engs, "pause", {"id": eid})["engagement"]["state"] == "paused"
    assert follow.engagement_op(engs, "resume", {"id": eid})["engagement"]["state"] == "active"
    assert follow.engagement_op(engs, "end", {"id": eid})["engagement"]["state"] == "ended"
    assert follow.engagement_op(engs, "list", {})["engagements"] == []
    assert not follow.engagement_op(engs, "get", {"id": "nope"})["ok"]


def test_new_engagements_use_the_follower_profile_from_policy(env):
    store, engs, _, _ = env
    assert follow.engagement_op(engs, "create", {"channel": "C1", "brief": "b"})["engagement"]["follower_profile"] == "slack-follow"
    store.update_policy(follower={"profile": "my-follower"})
    assert follow.engagement_op(engs, "create", {"channel": "C2", "brief": "b"})["engagement"]["follower_profile"] == "my-follower"


def test_owner_approved_post_bypasses_the_bot_cap_but_not_one_reply_per_message(env):
    store, engs, api, poster = env
    engs.create("C1", "a")
    for i in range(4):
        rec(store, "C1", f"{10 + i}.0", f"<@{BOTSELF}> q{i}", user="UOTHERBOT", thread="9.0", bot_id="BF")
    for i in range(3):
        assert poster.post("C1", f"a{i}", thread_ts="9.0", reply_to=f"{10 + i}.0")["ok"]
    assert not poster.post("C1", "a3", thread_ts="9.0", reply_to="13.0")["ok"]
    assert poster.post("C1", "a3", thread_ts="9.0", reply_to="13.0", owner_approved=True)["ok"]
    assert not poster.post("C1", "again", thread_ts="9.0", reply_to="13.0", owner_approved=True)["ok"]


def test_message_op_wakes_the_follower_with_the_note_once(env):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True, last_handled_ts="1.0")
    engs.update(e["id"], kickoff=0)
    r = follow.engagement_op(engs, "message", {"id": e["id"], "text": "result: 42", "sender": "@session:default@mac/s1"})
    assert r["ok"] and r["queued"] == 1
    spawned = []
    runner = make_runner(store, engs, spawned)
    assert runner.run_once(e["id"])
    assert "result: 42" in spawned[0][1] and "@session:default@mac/s1" in spawned[0][1]
    assert not runner.run_once(e["id"])  # delivered once
    assert follow.engagement_op(engs, "message", {"id": e["id"]})["ok"] is False  # needs text


def test_mbp_pull_claims_due_requests_once_and_done_settles_them(env):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    q = engs.queue_mbp(e["id"], "@session:default@mac/s1", "check X", "C1", "7.0")
    store.update_policy(follower={"mbp_delivery": "pull"})
    got = follow.engagement_op(engs, "mbp_pull", {})
    assert [x["id"] for x in got["requests"]] == [q["id"]] and got["requests"][0]["message"] == "check X"
    assert follow.engagement_op(engs, "mbp_pull", {})["requests"] == []  # claimed, not handed out twice
    follow.engagement_op(engs, "mbp_done", {"id": q["id"], "ok": False, "error": "hermes failed", "retry_in": 0})
    assert [x["id"] for x in follow.engagement_op(engs, "mbp_pull", {})["requests"]] == [q["id"]]
    follow.engagement_op(engs, "mbp_done", {"id": q["id"], "ok": True})
    assert engs.mbp_get(q["id"])["state"] == "delivered"


def test_runner_leaves_mbp_queue_to_the_puller_when_policy_says_pull(env, monkeypatch):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    engs.queue_mbp(e["id"], "@session:default@mac/s1", "m", "C1", "")
    r = make_runner(store, engs, [])
    sent = []
    monkeypatch.setattr(r, "_courier_send", lambda t, m: sent.append(t) or (True, ""))
    store.update_policy(follower={"mbp_delivery": "pull"})
    r.deliver_mbp()
    assert sent == []
    store.update_policy(follower={"mbp_delivery": "courier"})
    r.deliver_mbp()
    assert sent == ["@session:default@mac/s1"]


def test_courier_names_the_mac_for_this_device_owner_refs():
    assert follow.target_route("@session:default@this-device/s1") == "this-device"
    assert follow.courier_target("@session:default@this-device/s1", "david-mbp") == "@session:default@david-mbp/s1"
    assert follow.courier_target("@session:default@mac-mini/s1", "david-mbp") == "@session:default@mac-mini/s1"
    assert follow.courier_target("@session:alpha-follow/s1", "david-mbp") == "@session:alpha-follow/s1"


def test_courier_request_carries_route_so_only_that_mac_claims_it(env, tmp_path):
    store, engs, _, _ = env
    r = make_runner(store, engs, [])
    r.root_home = tmp_path
    import threading, json as _j
    def desktop():
        out = tmp_path / "session_mentions" / "outbox"
        for _ in range(100):
            files = list(out.glob("*.json")) if out.exists() else []
            if files:
                req = _j.loads(files[0].read_text())
                files[0].unlink()
                seen.append(req)
                (tmp_path / "session_mentions" / "results" / files[0].name).write_text(
                    _j.dumps({"ok": True, "queued": True, "status": "queued"}))
                return
            time.sleep(0.05)
    seen = []
    t = threading.Thread(target=desktop); t.start()
    assert r._courier_send("@session:default@david-mbp/s1", "hi") == (True, "queued", False)
    t.join()
    assert seen[0]["route"] == "david-mbp" and seen[0]["args"]["target"] == "@session:default@david-mbp/s1"


def test_courier_first_then_pull_fallback_never_both(env, monkeypatch):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    q = engs.queue_mbp(e["id"], "@session:default@this-device/s1", "m", "C1", "")
    r = make_runner(store, engs, [])
    sent = []
    # Default mode is courier with pull fallback: a fresh request is the courier's, not the puller's.
    assert follow.engagement_op(engs, "mbp_pull", {})["requests"] == []
    monkeypatch.setattr(r, "_courier_send", lambda t, m: sent.append(t) or (False, "Desktop courier offline", False))
    r.deliver_mbp()
    assert sent == ["@session:default@david-mbp/s1"]  # this-device -> the named Mac
    r.deliver_mbp()
    assert len(sent) == 1  # courier backs off
    got = follow.engagement_op(engs, "mbp_pull", {})["requests"]
    assert [x["id"] for x in got] == [q["id"]]  # failed courier send -> due for the puller at once
    r._courier_backoff.clear()
    r.deliver_mbp()
    assert len(sent) == 1  # claimed by the puller: courier keeps off it
    follow.engagement_op(engs, "mbp_done", {"id": q["id"], "ok": True})
    assert engs.mbp_get(q["id"])["state"] == "delivered"


def test_pull_takes_requests_the_courier_left_past_grace(env):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    q = engs.queue_mbp(e["id"], "@session:default@mac/s1", "m", "C1", "")
    assert engs.mbp_claim(now=time.time() + 10, courier_grace=follow.PULL_GRACE_S) == []
    assert [x["id"] for x in engs.mbp_claim(now=time.time() + follow.PULL_GRACE_S + 1,
                                            courier_grace=follow.PULL_GRACE_S)] == [q["id"]]
    store.update_policy(follower={"mbp_delivery": "courier_only"})
    assert follow.engagement_op(engs, "mbp_pull", {})["requests"] == []


def test_unknown_courier_outcome_is_never_resent(env, monkeypatch):
    store, engs, _, _ = env
    e = engs.create("C1", "x", private=True)
    q = engs.queue_mbp(e["id"], "@session:default@mac/s1", "m", "C1", "")
    r = make_runner(store, engs, [])
    monkeypatch.setattr(r, "_courier_send", lambda t, m: (False, "socket closed", True))
    r.deliver_mbp()
    assert engs.mbp_get(q["id"])["state"] == "uncertain"
    assert follow.engagement_op(engs, "mbp_pull", {})["requests"] == [] and engs.due_mbp(time.time() + 9999) == []


# ---- deterministic ack ---------------------------------------------------------------------------
def ev(ch, ts, text="hi", user="UBOB", thread=""):
    d = {"type": "message", "channel": ch, "ts": ts, "text": text, "user": user}
    if thread:
        d["thread_ts"] = thread
    return d


def test_mention_gets_eyes_without_an_llm(env):
    store, engs, api, _ = env
    n = follow.ack_if_addressed(store, api, ev("C1", "5.0", f"<@{BOTSELF}> status?"), BOTSELF)
    assert n and api.reactions == [("add", "eyes", "5.0")]


def test_unaddressed_chatter_gets_no_reaction(env):
    store, engs, api, _ = env
    assert not follow.ack_if_addressed(store, api, ev("C1", "5.0", "lunch anyone?"), BOTSELF)
    assert not follow.ack_if_addressed(store, api, ev("C1", "6.0", f"<@{BOTSELF}> x", user=BOTSELF), BOTSELF)
    assert api.reactions == []


def test_reply_in_a_thread_alpha_posted_in_gets_eyes_but_other_threads_do_not(env):
    store, engs, api, _ = env
    store.record_event({"type": "message", "channel": "C1", "ts": "7.0", "text": "ans", "user": BOTSELF,
                        "thread_ts": "5.0"})
    assert follow.ack_if_addressed(store, api, ev("C1", "8.0", "thanks, and why?", thread="5.0"), BOTSELF)
    assert not follow.ack_if_addressed(store, api, ev("C1", "9.0", "and why?", thread="1.0"), BOTSELF)
    assert api.reactions == [("add", "eyes", "8.0")]


def test_dm_channel_message_gets_eyes(env):
    store, engs, api, _ = env
    assert follow.ack_if_addressed(store, api, ev("D123", "5.0", "hey"), BOTSELF)


def test_ack_failure_never_raises(env):
    store, engs, api, _ = env
    def boom(m, p=None):
        raise RuntimeError("slack down")
    assert not follow.ack_if_addressed(store, boom, ev("C1", "5.0", f"<@{BOTSELF}> x"), BOTSELF)


def test_reply_swaps_eyes_for_check(env):
    store, engs, api, poster = env
    engs.create("C1", "a")
    rec(store, "C1", "5.0")
    assert poster.post("C1", "answer", thread_ts="5.0", reply_to="5.0")["ok"]
    assert api.reactions == [("remove", "eyes", "5.0"), ("add", "white_check_mark", "5.0")]


def test_reaction_trouble_does_not_fail_the_post(env):
    store, engs, api, poster = env
    engs.create("C1", "a")
    rec(store, "C1", "5.0")
    orig = api.__call__
    class Bad(type(api)):
        def __call__(self, method, params=None):
            if method.startswith("reactions."):
                raise RuntimeError("no_reaction")
            return orig(method, params)
    p2 = follow.Poster(engs, Bad(), lambda: BOTSELF)
    assert p2.post("C1", "answer", thread_ts="5.0", reply_to="5.0")["ok"]

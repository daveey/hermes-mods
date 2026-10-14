"""Tests for slack-workspace. Run: ~/code/hermes-agent/.venv/bin/python -m pytest plugins/slack-workspace -q"""

import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("slack_workspace_pkg", HERE / "__init__.py",
                                                  submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["slack_workspace_pkg"] = mod
    spec.loader.exec_module(mod)
    return mod


sw = _load()
core = sw.core


# ---------------------------------------------------------------------------------------------
class FakeSlack:
    """Minimal Web API double."""

    def __init__(self):
        self.calls = []
        self.channels = [
            {"id": "C1", "name": "general", "is_member": True},
            {"id": "C2", "name": "proj-x", "is_member": False},
            {"id": "C3", "name": "random", "is_member": False},
        ]
        self.users = {"UADMIN": "david", "UBOB": "bob", "UBOT": "alpha"}

    def __call__(self, token, method, params=None, **_):
        params = params or {}
        self.calls.append((method, params))
        if method == "conversations.list":
            return {"ok": True, "channels": [dict(c) for c in self.channels]}
        if method == "conversations.info":
            cid = params["channel"]
            if cid.startswith("G"):
                return {"ok": True, "channel": {"id": cid, "name": "secret", "is_private": True}}
            for c in self.channels:
                if c["id"] == cid:
                    return {"ok": True, "channel": c}
            raise core.SlackError(method, "channel_not_found")
        if method == "users.info":
            return {"ok": True, "user": {"id": params["user"], "name": self.users.get(params["user"], "x"),
                                         "profile": {"display_name": self.users.get(params["user"], "")}}}
        if method == "conversations.join":
            for c in self.channels:
                if c["id"] == params["channel"]:
                    c["is_member"] = True
            return {"ok": True, "channel": {"id": params["channel"]}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D" + params["users"]}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": str(time.time()), "channel": params["channel"]}
        return {"ok": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = FakeSlack()
    monkeypatch.setattr(core, "call", fake)
    st = sw.State(tmp_path)
    st.adapter = types.SimpleNamespace(
        _team_clients={"T1": types.SimpleNamespace(token="xoxb-test")}, _bot_user_id="UBOT",
        config=types.SimpleNamespace(extra={"allow_admin_from": ["UADMIN"]}))
    session = {"PLATFORM": "slack", "USER_ID": "UBOB", "CHAT_ID": "C1", "THREAD_ID": "", "MESSAGE_ID": ""}
    monkeypatch.setattr(sw, "_session", lambda n: session.get(n, ""))
    return st, fake, session


# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("method,tier", [
    ("conversations.history", "read"), ("users.info", "read"), ("search.messages", "read"),
    ("assistant.search.context", "read"), ("chat.getPermalink", "read"),
    ("chat.postMessage", "act"), ("reactions.add", "act"), ("conversations.join", "act"),
    ("conversations.setTopic", "admin"), ("conversations.archive", "admin"),
    ("users.profile.set", "admin"), ("usergroups.update", "admin"), ("admin.users.list", "admin"),
    ("totally.unknown", "admin"),
])
def test_method_tier(method, tier):
    assert core.method_tier(method) == tier


def test_store_records_edits_deletes_threads(tmp_path):
    s = core.Store(tmp_path)
    now = time.time()
    s.record_event({"type": "message", "channel": "C1", "ts": f"{now:.6f}", "user": "U1", "text": "hi"})
    s.record_event({"type": "message", "channel": "C1", "ts": f"{now + 1:.6f}", "user": "U2",
                    "text": "reply", "thread_ts": f"{now:.6f}"})
    s.record_event({"type": "message", "subtype": "message_changed", "channel": "C1",
                    "message": {"ts": f"{now:.6f}", "text": "hi (edited)"}})
    s.record_event({"type": "message", "channel": "C1", "ts": f"{now + 2:.6f}", "user": "U1", "text": "oops"})
    s.record_event({"type": "message", "subtype": "message_deleted", "channel": "C1",
                    "deleted_ts": f"{now + 2:.6f}"})
    s.record_event({"type": "member_joined_channel", "channel": "C1", "user": "U3"})
    rows = s.recent("C1", now - 10)
    assert [r["text"] for r in rows] == ["hi (edited)", "reply"]
    assert rows[0]["reply_count"] == 1 and rows[0]["edited"] == 1
    assert [e["type"] for e in s.events_since(now - 10)] == ["member_joined_channel"]
    assert s.activity_summary(now - 10)[0]["n"] == 2


def test_policy_merges_lists(tmp_path):
    s = core.Store(tmp_path)
    s.update_policy(auto_join=["proj-*"])
    p = s.update_policy(auto_join=["general"], retention_days=7)
    assert p["auto_join"] == ["general", "proj-*"] and p["retention_days"] == 7
    assert core.matches(p["auto_join"], "C9", "proj-x") and not core.matches(p["auto_join"], "C9", "x")


def test_join_non_admin_creates_one_bulk_request(env):
    st, fake, _ = env
    out = json.loads(sw.tool_join(st, {"channels": ["#proj-x", "random", "general", "nope"],
                                       "reason": "follow work"}))
    assert out["already_member"] == ["#general"] and out["joined"] == []
    assert out["problems"][0]["channel"] == "nope"
    rid = out["approval"]["request_id"]
    posts = [p for m, p in fake.calls if m == "chat.postMessage"]
    assert len(posts) == 1 and posts[0]["channel"] == "DUADMIN"
    assert not any(m == "conversations.join" for m, _ in fake.calls)
    # admin approves only the first one
    req = sw._resolve(st, rid, "UADMIN", "approve", {0})
    assert req["status"] == "approved" and [r["op"] for r in req["results"]] == ["join #proj-x"]
    assert [p["channel"] for m, p in fake.calls if m == "conversations.join"] == ["C2"]


def test_join_always_sets_auto_join(env):
    st, fake, _ = env
    rid = json.loads(sw.tool_join(st, {"channels": ["proj-x"]}))["approval"]["request_id"]
    sw._resolve(st, rid, "UADMIN", "always", None)
    assert st.store.policy()["auto_join"] == ["*"]
    out = json.loads(sw.tool_join(st, {"channels": ["random"]}))
    assert out["joined"] == ["#random"] and "approval" not in out


def test_admin_requester_joins_directly(env):
    st, fake, session = env
    session["USER_ID"] = "UADMIN"
    out = json.loads(sw.tool_join(st, {"channels": ["*"]}))
    assert sorted(out["joined"]) == ["#proj-x", "#random"]


def test_private_read_guard(env):
    st, fake, session = env
    out = json.loads(sw.tool_api(st, {"method": "conversations.history", "params": {"channel": "G9"}}))
    assert "private" in out["error"]
    session["USER_ID"] = "UADMIN"
    out = json.loads(sw.tool_api(st, {"method": "conversations.history", "params": {"channel": "G9"}}))
    assert out.get("ok") is True


def test_act_tiers(env):
    st, fake, _ = env
    here = json.loads(sw.tool_api(st, {"method": "reactions.add",
                                       "params": {"channel": "C1", "name": "eyes", "timestamp": "1"}}))
    assert here["ok"] is True
    elsewhere = json.loads(sw.tool_api(st, {"method": "chat.postMessage",
                                            "params": {"channel": "C3", "text": "hi"}}))
    assert elsewhere["status"] == "pending_approval"
    admin = json.loads(sw.tool_api(st, {"method": "conversations.setTopic",
                                        "params": {"channel": "C1", "topic": "x"}}))
    assert admin["status"] == "pending_approval" and admin["tier"] == "admin"


def test_non_admin_click_is_ignored(env):
    st, fake, _ = env
    rid = json.loads(sw.tool_join(st, {"channels": ["proj-x"]}))["approval"]["request_id"]
    import asyncio

    async def ack():
        pass

    h = sw._make_action_handler(st, "approve")
    asyncio.run(h(ack, {"user": {"id": "UBOB"}, "container": {"channel_id": "DUBOB"}}, {"value": rid}))
    assert st.store.request(rid)["status"] == "pending"
    asyncio.run(h(ack, {"user": {"id": "UADMIN"}, "state": {"values": {"sw_pick_0": {"sw_pick_0": {
        "type": "checkboxes", "selected_options": [{"value": "0"}]}}}}}, {"value": rid}))
    assert st.store.request(rid)["status"] == "approved"


def test_context_hook_injects_once(env):
    st, fake, session = env
    now = time.time()
    for i, (u, t) in enumerate([("UBOB", "deploy is broken"), ("UADMIN", "looking"), ("UBOT", "on it")]):
        st.store.record_event({"type": "message", "channel": "C1", "ts": f"{now - 60 + i:.6f}", "user": u,
                               "text": t})
    session["MESSAGE_ID"] = f"{now:.6f}"
    hook = sw._make_context_hook(st)
    ctx = hook(platform="slack", session_id="s1")["context"]
    assert "#general" in ctx and "bob: deploy is broken" in ctx and "you: on it" in ctx
    assert hook(platform="slack", session_id="s1") is None
    assert hook(platform="cli", session_id="s1") is None
    st.store.update_policy(quiet_channels=["general"])
    assert hook(platform="slack", session_id="s2") is None


def test_observer_middleware_records_and_continues(env):
    st, fake, _ = env
    import asyncio

    class App:
        def use(self, fn):
            self.mw = fn

    app = App()
    sw._make_factory(st)(app, st.adapter)
    called = []

    async def nxt():
        called.append(1)

    asyncio.run(app.mw({"type": "event_callback", "team_id": "T1", "event": {
        "type": "message", "channel": "C1", "ts": f"{time.time():.6f}", "user": "UBOB", "text": "yo"}}, nxt))
    assert called == [1] and st.store.recent("C1")[0]["text"] == "yo"

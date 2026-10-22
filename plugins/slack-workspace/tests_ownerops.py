"""Tests for owner-DM routing (ownerops.py): one per entry path.
Run: uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/slack-workspace"""

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


oo = _load("slack_workspace_ownerops_t", "ownerops.py")
OWNER = "U0OWNER"


class Spawns:
    def __init__(self):
        self.calls, self.done = [], threading.Event()

    def __call__(self, channel, thread, user, text):
        self.calls.append((channel, thread, user, text))
        self.done.set()
        return 0, "ok"


@pytest.fixture
def ops(tmp_path):
    sp = Spawns()
    o = oo.OwnerOps(tmp_path / "slack-workspace", owners=lambda: frozenset({OWNER}), bot_home=tmp_path / "profiles" / "alpha",
                    profile=lambda: "mac-ops", spawn=sp)
    o.sp = sp
    return o


def ev(text, user=OWNER, channel="D1", ctype="im", ts="10.0", **kw):
    return dict({"type": "message", "user": user, "channel": channel, "channel_type": ctype, "ts": ts, "text": text}, **kw)


def test_owner_dm_machine_request_is_routed_and_allowed(ops):
    assert ops.route(ev("my MBP display is weird")) is True
    assert ops.sp.done.wait(2)
    assert ops.sp.calls == [("D1", "10.0", OWNER, "my MBP display is weird")]  # answers in the message's own thread


def test_owner_in_a_channel_is_refused(ops):
    for ctype in ("channel", "group", "mpim"):
        assert ops.route(ev("my MBP display is weird", channel="C1", ctype=ctype)) is False
    assert ops.route(ev("my MBP display is weird", channel="C1", ctype="channel", thread_ts="9.0")) is False
    assert not ops.sp.calls


def test_other_users_dm_is_refused(ops):
    assert ops.route(ev("my MBP display is weird", user="U0STRANGER")) is False
    assert ops.route(ev("fix the mini reboot it", user="U0STRANGER", bot_id="B1")) is False
    assert not ops.sp.calls


def test_bot_and_edited_messages_are_not_owner_turns(ops):
    assert ops.route(ev("my MBP display is weird", bot_id="B9")) is False
    assert ops.route(ev("my MBP display is weird", subtype="message_changed")) is False
    assert not ops.sp.calls


def test_ordinary_owner_dm_stays_with_ambient_alpha(ops):
    assert ops.route(ev("summarize the quakes channel")) is False
    assert not ops.sp.calls


def test_no_owner_configured_means_no_routing(tmp_path):
    o = oo.OwnerOps(tmp_path, owners=lambda: frozenset(), bot_home=tmp_path / "profiles" / "alpha", spawn=Spawns())
    assert o.route(ev("my MBP display is weird")) is False


def test_followups_in_an_ops_thread_reach_ops_even_without_machine_words(ops):
    assert ops.route(ev("my MBP screen is frozen")) is True
    ops.sp.done.wait(2)
    assert ops.route(ev("yes", ts="11.0", thread_ts="10.0")) is True
    assert ops.route(ev("yes", ts="12.0", thread_ts="99.0")) is False  # a different thread is untouched
    assert ops.route(ev("yes", user="U0STRANGER", ts="13.0", thread_ts="10.0")) is False


def test_handoff_allowed_only_in_the_owner_dm(ops):
    def env_for(**over):
        base = {"HERMES_SESSION_PLATFORM": "slack", "HERMES_SESSION_CHAT_TYPE": "dm", "HERMES_SESSION_CHAT_ID": "D1",
                "HERMES_SESSION_USER_ID": OWNER, "HERMES_SESSION_THREAD_ID": ""}
        base.update(over)
        return lambda k: base.get(k, "")
    last = lambda ch: "50.0"
    ok = json.loads(oo.handoff_tool(ops, {"request": "fix my mouse"}, env=env_for(), last_ts=last))
    assert ok["ok"] and ops.sp.done.wait(2) and ops.sp.calls[0][:2] == ("D1", "50.0")
    n = len(ops.sp.calls)
    for bad in (env_for(HERMES_SESSION_CHAT_TYPE="channel", HERMES_SESSION_CHAT_ID="C1"),
                env_for(HERMES_SESSION_USER_ID="U0STRANGER"), env_for(HERMES_SESSION_PLATFORM="desktop"),
                env_for(HERMES_SESSION_CHAT_ID="C1")):
        assert not json.loads(oo.handoff_tool(ops, {"request": "fix my mouse"}, env=bad, last_ts=last))["ok"]
    assert len(ops.sp.calls) == n


def test_ambient_alpha_has_only_the_keyless_handoff_tool():
    """The slack-workspace plugin (loaded by ambient Alpha) must not contain the machine tools or the key."""
    for f in HERE.glob("*.py"):
        if f.name.startswith("tests_"):
            continue
        src = f.read_text()
        for needle in ("id_mac_fixer", "IdentitiesOnly", "def tool_diag", "def tool_fix", "def tool_screen", "run_shim"):
            assert needle not in src, (f.name, needle)
    spec = importlib.util.spec_from_file_location("mac_ops_amb", HERE.parent / "mac-ops" / "__init__.py")
    mo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mo)

    class Ctx:
        tools = []

        def register_tool(self, name, **kw):
            self.tools.append(name)
    mo._config = lambda: {"owner": {"slack_user_ids": [OWNER]}}  # ambient profile: no mac_ops.enabled
    c = Ctx()
    mo.register(c)
    assert c.tools == []

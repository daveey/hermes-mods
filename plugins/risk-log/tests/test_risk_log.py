"""Tests for risk-log. Run: uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/risk-log/tests"""

import importlib.util
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sys.modules.setdefault(name, mod)


redact = _load("risk_log_redact", "redact.py")
classify = _load("risk_log_classify", "classify.py")
store = _load("risk_log_store", "store.py")
plugin = _load("risk_log_plugin", "__init__.py")


def fake_detector(command):
    return (True, "recursive delete", "recursive delete") if "rm -rf" in command else (False, None, None)


# ── redaction ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret", [
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ghp_" + "a" * 36,
    "github_pat_" + "B" * 30,
    "xoxb-" + "1234567890-abcdefghij",
    "sk-ant-" + "api03-" + "x" * 30,
    "sk-or-v1-" + "f" * 40,
    "AIza" + "S" * 35,
    "tbk_" + "client.secretvalue123",
    "eyJ" + "hbGciOiJIUzI1" + ".eyJzdWIiOiIxMjM0" + ".SflKxwRJSMeKKF2QT4",
])
def test_vendor_secrets_are_redacted(secret):
    out = redact.prefix("curl -H x " + secret + " https://example.com", limit=500)
    assert secret not in out and "REDACTED" in out


def test_assignment_flag_and_url_credentials_redacted():
    out = redact.prefix("API_KEY=hunter2hunter2 mytool --token s3cr3tvalue https://bob:pa55word@h.example/x",
                        limit=500)
    assert "hunter2hunter2" not in out and "s3cr3tvalue" not in out and "pa55word" not in out
    assert out.startswith("API_KEY=") and "--token" in out


def test_prefix_is_80_chars_and_redacts_before_cutting():
    secret = "ghp_" + "z" * 36
    text = "x" * 70 + " " + secret
    out = redact.prefix(text)
    assert len(out) <= 80 and "zzzzzzzzzz" not in out


def test_plain_commands_keep_their_words():
    assert redact.prefix("token-broker exec --scope user/github --reason 'gh auth status' -- gh pr list") \
        .startswith("token-broker exec --scope user/github")


# ── classification ────────────────────────────────────────────────────────

def classes(tool, args):
    return [f.risk_class for f in classify.classify(tool, args, fake_detector)]


def test_credential_reads():
    assert classes("terminal", {"command": "cat ~/.ssh/config | head -0"}) == ["credential-read"]
    assert classes("terminal", {"command": "grep KEY ~/.hermes/.env"}) == ["credential-read"]
    assert classes("terminal", {"command": "security find-generic-password -s foo -w"}) == ["credential-read"]
    assert classes("read_file", {"path": "/Users/me/.hermes/auth.json"}) == ["credential-read"]
    assert classes("execute_code", {"code": "open(os.path.expanduser('~/.ssh/id_ed25519')).read()"}) == \
        ["credential-read"]
    assert classes("terminal", {"command": "ls ~/code; git status"}) == []
    assert classes("read_file", {"path": "/Users/me/code/app/environment.py"}) == []


def test_dangerous_command_uses_the_detector():
    assert classes("terminal", {"command": "rm -rf build"}) == ["dangerous-command"]
    found = classify.classify("terminal", {"command": "rm -rf build"}, fake_detector)[0]
    assert found.detail == {"pattern": "recursive delete"}
    assert classes("terminal", {"command": "rm -rf x"}) == ["dangerous-command"]
    assert classify.classify("terminal", {"command": "rm -rf x"}, None) == []  # no detector, no class


def test_detector_sees_a_bounded_head():
    seen = []
    classify.classify("terminal", {"command": "rm -rf x; " + "y" * 100000}, lambda c: seen.append(len(c)) or
                      (False, None, None))
    assert seen == [classify.DETECT_MAX]


def test_broker_scope_is_kept():
    found = classify.classify("terminal", {"command": "~/.local/bin/token-broker exec --scope user/github "
                                                      "--reason 'x' -- gh api user"}, fake_detector)
    assert [(f.risk_class, f.detail["scope"]) for f in found] == [("broker-vend", "user/github")]
    assert classes("terminal", {"command": "token-broker scopes"}) == []
    dynamic = classify.classify("terminal", {"command": "token-broker exec --scope user/$NAME -- x"}, None)
    assert [(f.risk_class, f.detail["scope"]) for f in dynamic] == [("broker-vend", None)]


def test_tool_name_classes():
    assert classes("mcp_chrome_devtools_click", {"uid": "1"}) == ["real-browser"]
    assert classify.classify("mcp__chrome_devtools__take_snapshot", {})[0].level == "medium"
    assert classes("mcp__whatsapp_personal__send_message", {"recipient": "x", "message": "hi"}) == ["message-send"]
    assert classes("mcp__whatsapp_personal__list_chats", {}) == []
    assert classes("send_message", {"target": "discord:123", "message": "hi"}) == ["message-send"]
    assert classes("mcp_autoresearch_buy_credits", {"amount": 5}) == ["spend-remote-exec"]
    assert classes("mcp_autoresearch_set_auto_topup", {}) == ["spend-remote-exec"]
    assert classes("mcp_autoresearch_list_nodes", {}) == []
    assert classes("web_search", {"query": "~/.ssh"}) == []


def test_bridge_calls_are_expanded():
    args = {"calls": [{"name": "mcp__chrome_devtools__navigate_page", "arguments": {"url": "https://x"}},
                      {"name": "mcp__whatsapp_bb__send_message", "arguments": {}}]}
    assert classes("tool_call", args) == ["real-browser", "message-send"]


# ── store / hook ──────────────────────────────────────────────────────────

def test_rows_hold_hash_and_redacted_prefix_only(tmp_path):
    secret = "ghp_" + "q" * 36
    cmd = "cat ~/.ssh/config && echo " + secret
    rows = store.rows_for({"tool": "terminal", "args": {"command": cmd}, "at": 1.0, "call_id": "c1",
                           "origin": "interactive"}, fake_detector)
    assert len(rows) == 1
    row = rows[0]
    assert row["cmd_sha256"] == redact.sha256(cmd)
    assert secret not in json.dumps(row) and len(row["cmd_prefix"]) <= 80


def test_writer_writes_0600_and_export_drops_prefix(tmp_path):
    db_path = tmp_path / "risk" / "tool-risk.sqlite"
    writer = store.Writer(db_path, detector=fake_detector)
    writer.submit({"tool": "terminal", "args": {"command": "cat ~/.ssh/config | head -0"}, "at": time.time(),
                   "call_id": "c2", "origin": "cron", "host": "h", "profile": "default", "status": "ok"})
    assert writer.flush(5)
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600
    rows = store.export(0, db_path)
    assert [(r["risk_class"], r["origin"], r["status"]) for r in rows] == [("credential-read", "cron", "ok")]
    assert "cmd_prefix" not in rows[0]
    assert store.export(0, db_path, prefix=True)[0]["cmd_prefix"].startswith("cat ~/.ssh/config")


def test_hook_is_fast_and_never_blocks(tmp_path, monkeypatch):
    class SlowWriter(store.Writer):
        def _run(self):  # a wedged writer thread (disk stall) must not slow calls
            time.sleep(60)
    monkeypatch.setattr(plugin, "WRITER", SlowWriter(tmp_path / "x.sqlite"))
    hooks = {}
    plugin.register(type("Ctx", (), {"register_hook": lambda self, name, fn: hooks.setdefault(name, fn)})())
    assert hooks == {"post_tool_call": plugin._post_tool_call}
    args = {"command": "cat ~/.ssh/config | head -0"}
    times = []
    for _ in range(QUEUE_N := 3000):  # more than the queue holds: overflow drops, never waits
        t0 = time.perf_counter()
        assert plugin._post_tool_call("terminal", args, session_id="s", tool_call_id="c") is None
        times.append(time.perf_counter() - t0)
    times.sort()
    # p99, not max: on a loaded host (load avg 200+) the OS deschedules even a bare dict copy for 100+ ms.
    assert times[len(times) // 2] < 0.0005 and times[int(len(times) * 0.99)] < 0.005, (times[1500], times[2970])
    assert plugin.WRITER.dropped >= QUEUE_N - store.QUEUE_MAX - 1


def test_logging_failure_never_changes_the_call(monkeypatch):
    class Broken:
        def submit(self, event):
            raise RuntimeError("disk full")
    monkeypatch.setattr(plugin, "WRITER", Broken())
    assert plugin._post_tool_call("terminal", {"command": "cat ~/.ssh/id_rsa"}) is None
    monkeypatch.setattr(plugin, "origin", lambda: 1 / 0)
    assert plugin._post_tool_call("terminal", {"command": "cat ~/.ssh/id_rsa"}) is None


def test_writer_survives_unwritable_db(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    writer = store.Writer(blocker / "sub" / "x.sqlite", detector=fake_detector)  # parent is a file
    writer.submit({"tool": "terminal", "args": {"command": "cat ~/.ssh/config"}, "at": 1.0, "origin": "x"})
    assert writer.flush(5) and writer.errors == 1


def test_irrelevant_tools_skip_everything(monkeypatch):
    calls = []
    monkeypatch.setattr(plugin.WRITER, "submit", calls.append)
    plugin._post_tool_call("web_search", {"query": "x"})
    plugin._post_tool_call("todo", {})
    assert calls == []


@pytest.mark.parametrize("env,expected", [
    ({"HERMES_CRON_SESSION": "1", "HERMES_KANBAN_TASK": "t_1"}, "cron"),
    ({"HERMES_KANBAN_TASK": "t_1"}, "kanban"),
    ({"HERMES_SESSION_PLATFORM": "discord"}, "gateway:discord"),
    ({"HERMES_SESSION_PLATFORM": "api_server"}, "api_server"),
    ({"HERMES_SESSION_SOURCE": "desktop"}, "interactive"),
    ({}, "interactive"),
])
def test_origin_labels(monkeypatch, env, expected):
    for key in ("HERMES_CRON_SESSION", "HERMES_KANBAN_TASK", "HERMES_SESSION_PLATFORM", "HERMES_PLATFORM",
                "HERMES_SESSION_SOURCE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert plugin.origin()[0] == expected


def test_profile_home_resolves_to_host_root(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_RISK_LOG", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "bb-dev"))
    assert store.default_path() == tmp_path / "risk" / "tool-risk.sqlite"


def test_long_random_runs_redacted_but_paths_and_shas_kept():
    key = "Zx9" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1yZ"
    out = redact.prefix("export X " + key, limit=500)
    assert key not in out
    path = "cd ~/.hermes/skills/software-development/babybear-development/scripts"
    assert redact.prefix(path, limit=500) == path
    sha = "git show 0389fb99ee864b65e39e6d241c82d42cc56573ed"
    assert redact.prefix(sha, limit=500) == sha

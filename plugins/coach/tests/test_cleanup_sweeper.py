"""cleanup_sweeper (self-heal agent-a5ac5f1cac): an agent that schedules a recurring cleanup job (cron, cronjob_manage,
systemd timer, launchd interval) gets a once-per-session hint to find and fix the producer of the leak instead.

Incident: 6 stray "Latency probe check" chats on hermes-hub; the agent's fix was a */5 crontab line running
`hermes sessions archive --title "Latency probe"`. The real cause was a test harness that never deleted its chats."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rules = load("coach_rules_sweeper_test", ROOT / "agent_rules.py")

OK = json.dumps({"output": "", "exit_code": 0})
FAIL = json.dumps({"output": "crontab: permission denied", "exit_code": 1})
CRON_OK = json.dumps({"success": True, "job_id": "abc123"})
CRON_FAIL = json.dumps({"success": False, "error": "invalid schedule"})

# the incident's exact turn-1 call (session 20261111_065313_d71b45)
INCIDENT = ("(crontab -l 2>/dev/null; echo '*/5 * * * * hermes sessions archive --title \"Latency probe\" "
            ">/dev/null 2>&1') | crontab -")

FIRES = [
    ("terminal", {"command": INCIDENT}),
    ("terminal", {"command": "printf '0 * * * * pkill -f stale-worker\\n' > /tmp/c.txt && crontab /tmp/c.txt"}),
    ("terminal", {"command": (
        "cat > ~/.config/systemd/user/scratch-gc.service <<'EOF'\n[Service]\n"
        "ExecStart=/usr/bin/find /tmp/scratch -mtime +1 -delete\nEOF\n"
        "cat > ~/.config/systemd/user/scratch-gc.timer <<'EOF'\n[Timer]\nOnCalendar=hourly\nEOF\n"
        "systemctl --user enable --now scratch-gc.timer")}),
    ("terminal", {"command": "systemd-run --user --on-calendar='*:0/10' hermes sessions prune --older-than 1d"}),
    ("cronjob_manage", {"action": "create", "schedule": "every 30m", "name": "sweep",
                        "prompt": "Run `hermes sessions archive --title 'Latency probe'` and report nothing."}),
    ("cronjob_manage", {"action": "update", "job_id": "abc123",
                        "prompt": "Delete every leftover worktree under ~/scratch."}),
    ("write_file", {"path": "/Users/you/Library/LaunchAgents/com.you.sweep.plist", "content": (
        "<plist><dict><key>Label</key><string>com.you.sweep</string><key>ProgramArguments</key><array>"
        "<string>/bin/rm</string><string>-rf</string><string>/tmp/probe-chats</string></array>"
        "<key>StartInterval</key><integer>300</integer></dict></plist>")}),
]

QUIET = [
    ("terminal", {"command": "crontab -l"}, OK),
    ("terminal", {"command": "(crontab -l; echo '0 3 * * * tar czf /backup/home.tgz ~/data') | crontab -"}, OK),
    ("terminal", {"command": "(crontab -l; echo '0 9 * * * python3 ~/bin/daily_report.py') | crontab -"}, OK),
    ("terminal", {"command": "crontab -l | grep -v 'sessions archive' | crontab -"}, OK),
    ("terminal", {"command": "hermes sessions archive --title 'Latency probe'"}, OK),
    ("terminal", {"command": "rm -rf /tmp/x && find /tmp -name '*.log' -delete"}, OK),
    ("cronjob_manage", {"action": "create", "schedule": "in 30m", "prompt": "Delete the temp branch."}, CRON_OK),
    ("cronjob_manage", {"action": "create", "schedule": "every day at 9am", "prompt": "Summarize my inbox."},
     CRON_OK),
    ("cronjob_manage", {"action": "remove", "job_id": "abc123"}, CRON_OK),
    ("cronjob_manage", {"action": "list"}, CRON_OK),
    ("terminal", {"command": INCIDENT}, FAIL),
    ("cronjob_manage", {"action": "create", "schedule": "every 30m", "prompt": "archive the stray chats"}, CRON_FAIL),
    ("write_file", {"path": "/Users/you/Library/LaunchAgents/com.you.once.plist", "content": (
        "<plist><dict><key>ProgramArguments</key><array><string>/bin/rm</string><string>/tmp/x</string></array>"
        "<key>RunAtLoad</key><true/></dict></plist>")}, OK),
    ("terminal", {"command": "systemctl --user enable --now backup.timer"}, OK),
]


def coach():
    return rules.AgentCoach(None, signals=None)


@pytest.mark.parametrize("tool,args", FIRES)
def test_fires_on_each_recurring_cleanup_shape(tool, args):
    c = coach()
    c.observe("s1", tool, args, CRON_OK if tool == "cronjob_manage" else OK)
    hint = c.take("s1") or ""
    assert "recurring cleanup" in hint and "five-whys" in hint


@pytest.mark.parametrize("tool,args,result", QUIET)
def test_quiet_on_non_cleanup_or_failed_or_one_shot(tool, args, result):
    c = coach()
    c.observe("s1", tool, args, result)
    assert "recurring cleanup" not in (c.take("s1") or "")


def test_timer_enabled_after_unit_written_in_an_earlier_call():
    c = coach()
    c.observe("s1", "write_file", {"path": "/home/u/.config/systemd/user/probe-gc.service",
                                   "content": "[Service]\nExecStart=hermes sessions archive --title probe\n"}, OK)
    assert c.take("s1") is None  # a unit file alone schedules nothing
    c.observe("s1", "terminal", {"command": "systemctl --user enable --now probe-gc.timer"}, OK)
    assert "recurring cleanup" in (c.take("s1") or "")


def test_incident_replay_hint_names_five_whys_and_the_producer_once():
    c = coach()
    c.observe("s1", "terminal", {"command": "crontab -l"}, OK)
    assert c.take("s1") is None
    c.observe("s1", "terminal", {"command": INCIDENT}, OK)
    hint = c.take("s1") or ""
    assert "five-whys" in hint and "producer" in hint and "remove this job" in hint
    c.observe("s1", "cronjob_manage", FIRES[4][1], CRON_OK)
    assert c.take("s1") is None  # once per session


def test_hint_lands_on_the_same_call_via_transform():
    c = coach()
    c.check_cleanup_sweeper("s1", "terminal", {"command": INCIDENT}, OK)
    assert "recurring cleanup" in (c.take("s1") or "")
    c2 = coach()
    c2.check_cleanup_sweeper("s2", "terminal", {"command": INCIDENT}, FAIL)
    assert c2.take("s2") is None


def test_transform_hook_wires_the_rule():
    src = (ROOT / "__init__.py").read_text()
    body = src[src.index("def transform_tool_result"):]
    body = body[:body.index("\n    def ", 10)]
    assert "check_cleanup_sweeper" in body


def test_tool_call_wrapped_cronjob_fires():
    c = coach()
    c.observe("s1", "tool_call", {"calls": [{"name": "cronjob_manage", "arguments": {
        "action": "create", "schedule": "*/5 * * * *", "no_agent": True,
        "script": "hermes sessions archive --title 'Latency probe'"}}]}, json.dumps([CRON_OK]))
    assert "recurring cleanup" in (c.take("s1") or "")


def test_readme_documents_the_rule():
    assert "`cleanup_sweeper`" in (ROOT / "README.md").read_text()

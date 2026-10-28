#!/usr/bin/env python3
"""Claude Code PreToolUse(Bash) hook: same live-SQLite rule as the Hermes plugin.

settings.json:  {"matcher": "Bash", "hooks": [{"type": "command",
                 "command": "python3 <this file>", "timeout": 10}]}
Exit 2 + stderr blocks the call and shows the agent the reason.
"""

import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("sqlite_live_guard_guard", Path(__file__).resolve().with_name("guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

try:
    payload = json.load(sys.stdin)
    cmd = payload.get("tool_input", {}).get("command", "") or ""
    cwd = payload.get("cwd", "") or ""
except Exception:
    sys.exit(0)

why = guard.check_shell(cmd, cwd) if guard.enabled() else None
if why:
    sys.stderr.write(guard.message(why) + "\n")
    sys.exit(2)
sys.exit(0)

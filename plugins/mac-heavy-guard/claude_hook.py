#!/usr/bin/env python3
"""Claude Code PreToolUse(Bash) hook: same heavy-work rule as the Hermes plugin.

settings.json:  {"matcher": "Bash", "hooks": [{"type": "command",
                 "command": "python3 <this file>", "timeout": 10}]}
Exit 2 + stderr blocks the call and shows the agent the reason. A multi-file Python test run in a Hermes core
worktree is not blocked: stdout carries hookSpecificOutput.additionalContext pointing at `hermes-core-wt test`.
"""

import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("mac_heavy_guard_guard", Path(__file__).resolve().with_name("guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

try:
    payload = json.load(sys.stdin)
    cmd = payload.get("tool_input", {}).get("command", "") or ""
    # The session's shell directory (after any `cd` in an earlier Bash call). The hook's own process cwd
    # need not be it, and `./ci.sh` must be read from where the shell will run it.
    cwd = payload.get("cwd")
except Exception:
    sys.exit(0)

cwd = cwd if isinstance(cwd, str) and cwd else None
block, note = guard.evaluate(cmd, cwd=cwd) if guard.enabled() else (None, None)
if block:
    sys.stderr.write(block + "\n")
    sys.exit(2)
if note:
    # Non-blocking: no permissionDecision, so the normal permission flow applies; Claude Code adds
    # additionalContext to the model's context next to the tool result.
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note}}))
sys.exit(0)

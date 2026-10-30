#!/usr/bin/env python3
"""Claude Code PreToolUse hook: same live-checkout rule as the Hermes plugin.

Install (idempotent; edits ~/.claude/settings.json, or --settings PATH):
    python3 <this file> --install [--settings PATH]
Adds PreToolUse entries for Bash and for Write|Edit|MultiEdit|NotebookEdit. Exit 2 + stderr blocks the call.
Protected root: $LIVE_CHECKOUT_ROOT (default ~/code/hermes-mods). Uses the payload's `cwd`. Fails open on bad input.
"""

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("live_checkout_guard_guard", HERE / "guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

EDIT_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"


def install(settings_path: Path) -> None:
    data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    entries = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    cmd = f"python3 {HERE / 'claude_hook.py'}"
    for matcher in ("Bash", EDIT_MATCHER):
        if any(h.get("command") == cmd and e.get("matcher") == matcher for e in entries for h in e.get("hooks", [])):
            continue
        entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"live-checkout-guard hooks installed in {settings_path}")


def main() -> int:
    argv = sys.argv[1:]
    if "--install" in argv:
        sp = Path(argv[argv.index("--settings") + 1]) if "--settings" in argv else Path.home() / ".claude/settings.json"
        install(sp)
        return 0
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name") or "Bash"
        ti = payload.get("tool_input", {}) or {}
        cwd = payload.get("cwd") or None
        if not guard.enabled():
            return 0
        if tool == "Bash":
            why = guard.check_shell(ti.get("command", "") or "", cwd)
        else:
            why = guard.check_file_tool(tool, {"path": ti.get("file_path") or ti.get("notebook_path")}, cwd)
    except Exception:
        return 0
    if why:
        sys.stderr.write(guard.message(why) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Claude Code PreToolUse(Bash) adapter for service-owner-guard (stdin JSON -> deny JSON on stdout).

``--install [SETTINGS]`` merges one Bash hook into Claude's settings (idempotent). Normal invocation only
reads the tool JSON; it never executes the command.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import sys
from pathlib import Path


def load_guard():
    spec = importlib.util.spec_from_file_location("service_owner_guard", Path(__file__).resolve().with_name("__init__.py"))
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def install(settings: Path) -> bool:
    settings = settings.resolve()
    data = json.loads(settings.read_text()) if settings.exists() else {}
    entries = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    for entry in entries:
        for hook in entry.get("hooks", []):
            if hook.get("type") != "command":
                continue
            try:
                argv = shlex.split(hook.get("command", ""))
            except ValueError:
                continue
            if entry.get("matcher") == "Bash" and any(
                    Path(a).expanduser().resolve() == Path(__file__).resolve() for a in argv):
                return False
    entries.append({"matcher": "Bash", "hooks": [{
        "type": "command", "command": "python3 " + shlex.quote(str(Path(__file__).absolute())), "timeout": 20}]})
    tmp = settings.with_name(settings.name + ".service-owner-guard.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.chmod(settings.stat().st_mode & 0o777 if settings.exists() else 0o600)
        os.replace(tmp, settings)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        settings = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else Path.home() / ".claude/settings.json"
        print("installed" if install(settings) else "already installed")
        return 0
    try:
        payload = json.load(sys.stdin)
    except (ValueError, TypeError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    if payload.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return 0
    guard = load_guard()
    why = guard.classify("terminal", payload.get("tool_input")) if guard.enabled() else None
    if why:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

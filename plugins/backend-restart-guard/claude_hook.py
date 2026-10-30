#!/usr/bin/env python3
"""Claude Code PreToolUse(Bash) adapter for the shared backend restart classifier.

Run with --install [SETTINGS] to merge one hook into Claude's existing settings.
Normal invocation only reads synthetic/tool JSON; it NEVER executes the command.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys


def load_guard():
    spec = importlib.util.spec_from_file_location(
        "backend_restart_guard", Path(__file__).resolve().with_name("__init__.py")
    )
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def install(settings: Path) -> bool:
    """Preserve other settings/hooks; repeated installation is a no-op."""
    settings = settings.resolve()  # keep a managed settings symlink intact
    data = json.loads(settings.read_text()) if settings.exists() else {}
    if data.get("hooks") is None:
        data["hooks"] = {}
    if data["hooks"].get("PreToolUse") is None:
        data["hooks"]["PreToolUse"] = []
    entries = data["hooks"]["PreToolUse"]
    # Match both a plugin symlink and its resolved source to avoid duplicate hooks.
    for entry in entries:
        for hook in entry.get("hooks", []):
            if hook.get("type") != "command":
                continue
            try:
                argv = shlex.split(hook.get("command", ""))
            except ValueError:
                continue
            if entry.get("matcher") == "Bash" and any(
                Path(arg).expanduser().resolve() == Path(__file__).resolve() for arg in argv
            ):
                return False
    entries.append({"matcher": "Bash", "hooks": [{
        "type": "command", "command": "python3 " + shlex.quote(str(Path(__file__).absolute())),
        "timeout": 10,
    }]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings.with_name(settings.name + ".backend-restart-guard.tmp")
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
        print(f"{guard.MESSAGE}\n({why})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

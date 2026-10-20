"""ha-critical-gate: physical-security Home Assistant calls go through a confirm gate instead of straight to HA.

The ``homeassistant`` plugin's ``ha_call_service`` sends any service with its own long-lived token; its only guard
is a shell/script domain block list, so an injected page could have an agent unlock a door. This ``pre_tool_call``
hook asks a configured gate command about every ``ha_call_service`` call (it also logs them):

- ``allow``: the call runs unchanged (lights, media, climate, closing and locking...).
- ``hold`` / ``deny``: the call is blocked; the gate's message becomes the tool result (e.g. "waiting for a tap on
  the owner's phone; it runs by itself if approved").

With no gate configured, or when the gate fails, opening-direction security calls (unlock/open a lock, disarm an
alarm, open/toggle/move a cover) are blocked and everything else runs. Nothing is removed from the toolset.

Gate command: ``HA_CRITICAL_GATE_COMMAND`` (a shell-style command line) or ``<hermes home>/ha-critical-gate.json``
``{"command": [argv...], "timeout": 25}``. It gets ``{"domain","service","entity_id","data","agent"}`` as JSON on
stdin and prints ``{"decision": "allow"|"hold"|"deny", "message": "..."}``.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOOL = "ha_call_service"
WRAPPERS = {"tool_call", "mcp__tool_call"}
CRITICAL_SERVICES = {
    "lock": {"unlock", "open"},
    "alarm_control_panel": {"alarm_disarm"},
    "cover": {"open_cover", "toggle", "set_cover_position", "open_cover_tilt", "set_cover_tilt_position",
              "toggle_cover_tilt"},
}
CONFIG_NAME = "ha-critical-gate.json"
DEFAULT_TIMEOUT = 25
NO_GATE = ("BLOCKED, nothing was sent: {what} needs the owner's confirmation and no confirmation gate is "
           "available right now. Do not retry it or call Home Assistant another way; tell the user.")


def critical(domain: str, service: str) -> bool:
    return service in CRITICAL_SERVICES.get(domain, ())


def _homes() -> list[Path]:
    homes = []
    try:
        from hermes_constants import get_hermes_home
        homes.append(Path(get_hermes_home()))
    except Exception:
        pass
    if os.environ.get("HERMES_HOME"):
        homes.append(Path(os.environ["HERMES_HOME"]))
    homes.append(Path.home() / ".hermes")
    out = []
    for home in homes:
        out.append(home)
        if home.parent.name == "profiles":       # a profile falls back to its root home's config
            out.append(home.parent.parent)
    return list(dict.fromkeys(out))


def agent_name() -> str:
    for home in _homes():
        if home.parent.name == "profiles":
            return home.name
    return "default"


def gate_config() -> Optional[dict]:
    command = os.environ.get("HA_CRITICAL_GATE_COMMAND", "").strip()
    if command:
        return {"command": shlex.split(command), "timeout": DEFAULT_TIMEOUT}
    for home in _homes():
        path = home / CONFIG_NAME
        try:
            config = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(config, dict) and isinstance(config.get("command"), list) and config["command"]:
            return {"command": [str(part) for part in config["command"]],
                    "timeout": config.get("timeout") if isinstance(config.get("timeout"), (int, float)) else DEFAULT_TIMEOUT}
    return None


def _call_of(args: Any) -> Optional[dict]:
    args = args if isinstance(args, dict) else {}
    domain, service = str(args.get("domain") or "").strip(), str(args.get("service") or "").strip()
    if not domain or not service:
        return None
    data = args.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = None
    return {"domain": domain, "service": service, "entity_id": args.get("entity_id"),
            "data": data if isinstance(data, dict) else None}


def ask_gate(call: dict, config: dict, run=subprocess.run) -> dict:
    """The gate's decision; raises on any failure (the caller fails closed for critical calls)."""
    done = run(config["command"], input=json.dumps(dict(call, agent=agent_name())), capture_output=True, text=True,
               timeout=config.get("timeout") or DEFAULT_TIMEOUT, check=False)
    if done.returncode != 0:
        raise RuntimeError("gate exited %s" % done.returncode)
    reply = json.loads((done.stdout or "").strip().splitlines()[-1])
    if not isinstance(reply, dict) or reply.get("decision") not in ("allow", "hold", "deny"):
        raise RuntimeError("gate gave no decision")
    return reply


def decide(args: Any, run=subprocess.run, config=None) -> Optional[str]:
    """None to let the call run, else the block message."""
    call = _call_of(args)
    if call is None:
        return None
    what = "%s.%s" % (call["domain"], call["service"])
    is_critical = critical(call["domain"], call["service"])
    config = config if config is not None else gate_config()
    if not config:
        return NO_GATE.format(what=what) if is_critical else None
    try:
        reply = ask_gate(call, config, run)
    except Exception:
        logger.warning("ha-critical-gate: gate failed for %s", what, exc_info=True)
        return NO_GATE.format(what=what) if is_critical else None
    if reply["decision"] == "allow":
        return None
    return str(reply.get("message") or ("BLOCKED, nothing was sent: %s was refused by the gate." % what))


def classify(tool_name: str, args: Any, run=subprocess.run, config=None) -> Optional[str]:
    if tool_name == TOOL:
        return decide(args, run, config)
    if tool_name in WRAPPERS and isinstance(args, dict):
        for call in args.get("calls") or []:
            if isinstance(call, dict) and str(call.get("name") or "") == TOOL:
                why = decide(call.get("arguments"), run, config)
                if why:
                    return why
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    if tool_name != TOOL and tool_name not in WRAPPERS:
        return None
    try:
        why = classify(tool_name, args)
    except Exception:
        logger.exception("ha-critical-gate failed")
        call = _call_of(args) if tool_name == TOOL else None
        if call and critical(call["domain"], call["service"]):
            return {"action": "block", "message": NO_GATE.format(what="%s.%s" % (call["domain"], call["service"]))}
        return None
    if not why:
        return None
    logger.info("ha-critical-gate blocked %s", tool_name)
    return {"action": "block", "message": why}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)

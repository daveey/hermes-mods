"""ha-critical-gate: physical-security Home Assistant calls go through a confirm gate instead of straight to HA.

The ``homeassistant`` plugin's ``ha_call_service`` sends any service with its own long-lived token; its only guard
is a shell/script domain block list, so an injected page could have an agent unlock a door. This ``pre_tool_call``
hook asks a configured gate command about every ``ha_call_service`` call (it also logs them):

- ``allow``: the call runs unchanged (lights, media, climate, closing and locking...).
- ``hold`` / ``deny``: the call is blocked; the gate's message becomes the tool result (e.g. "waiting for a tap on
  the owner's phone; it runs by itself if approved").

With no trusted gate, only SAFE_DOMAINS (light, media_player, climate), lock.lock,
cover.close_cover/stop_cover/close_cover_tilt/stop_cover_tilt, alarm_control_panel's
alarm_arm_home/alarm_arm_away/alarm_arm_night, and generic homeassistant calls with explicit
resolved targets only in SAFE_DOMAINS remain available. Every other domain/service,
including unknown and raw-protocol integrations, needs confirmation and is blocked without a gate.
Targets in entity_id and data.entity_id still override safe domains. Direct calls, batched and
single-object calls, JSON-string arguments/calls and legacy {name, arguments} wrappers are
classified here. Entry names are stripped, case-insensitive and accept an mcp__ prefix;
misplaced names inside arguments are lifted from nested envelopes or beside call parameters.
Nameless entries mentioning ha_call_service fail closed; explicit other tools are unaffected.
Unparseable wrappers naming ha_call_service are blocked without relying on core unwrapping.

Gate command comes ONLY from root-owned, non-agent-writable ``/etc/hermes/ha-critical-gate.json``:
``{"command": ["/absolute/trusted/gate"], "timeout": 25}``. Home/profile files and
``HA_CRITICAL_GATE_COMMAND`` are ignored. The executable and all path ancestors must also be root-owned,
non-agent-writable and not symlinks. It gets ``{"domain","service","entity_id","data","agent"}`` on
stdin and prints ``{"decision": "allow"|"hold"|"deny", "message": "..."}``.
"""

from __future__ import annotations

import json
import logging
import os
import math
import stat
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
CONFIG_NAME = "ha-critical-gate.json"  # legacy filename; home files are never authority
TRUSTED_CONFIG = Path("/etc/hermes/ha-critical-gate.json")
OPAQUE_ACTUATORS = {"script", "scene", "automation", "button", "switch"}
SAFE_DOMAINS = {"light", "media_player", "climate"}
SAFE_SERVICES = {
    "lock": {"lock"},
    "cover": {"close_cover", "stop_cover", "close_cover_tilt", "stop_cover_tilt"},
    "alarm_control_panel": {"alarm_arm_home", "alarm_arm_away", "alarm_arm_night"},
}
DEFAULT_TIMEOUT = 25
NO_GATE = ("BLOCKED, nothing was sent: {what} needs the owner's confirmation and no confirmation gate is "
           "available right now. Do not retry it or call Home Assistant another way; tell the user.")


def critical(domain: str, service: str, entity_id=None, data=None) -> bool:
    # HA normalizes entity IDs to lowercase; classification must see the same target.
    domain, service = domain.lower(), service.lower()
    if domain in OPAQUE_ACTUATORS or service in CRITICAL_SERVICES.get(domain, ()):
        return True
    data = data if isinstance(data, dict) else {}
    targets = []
    unresolved = False
    for value in (entity_id, data.get("entity_id")):
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        if not values:
            unresolved = True
        for item in values:
            if not isinstance(item, str):
                unresolved = True
                continue
            targets.extend(part.strip().lower() for part in item.split(","))
    for target in targets:
        target_domain, dot, name = target.partition(".")
        if not dot or not name or target_domain == "group":
            unresolved = True
        if target_domain in OPAQUE_ACTUATORS:
            return True
        if target_domain in {"lock", "alarm_control_panel", "cover"}:
            safe = {"lock": {"lock"}, "alarm_control_panel": {"alarm_arm_home", "alarm_arm_away", "alarm_arm_night"},
                    "cover": {"close_cover", "stop_cover", "close_cover_tilt", "stop_cover_tilt"}}
            if service not in safe[target_domain]:
                return True
    # Generic HA services expand indirect/implicit targets server-side. Unknown
    # nonempty selectors fail closed too, so a new HA selector cannot bypass us.
    if domain == "homeassistant":
        return (unresolved or not targets
                or any(target.partition(".")[0] not in SAFE_DOMAINS for target in targets)
                or any(value for key, value in data.items() if key not in {"entity_id", "exclude"}))
    # Never infer physical safety from an unfamiliar integration/service name.
    return domain not in SAFE_DOMAINS and service not in SAFE_SERVICES.get(domain, ())


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


def _check_metadata(info, path: Path, *, directory: bool) -> None:
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (info.st_uid != 0 or info.st_mode & 0o022 or not kind(info.st_mode)
            or os.access(path, os.W_OK)):
        raise ValueError("gate path must be root-owned and not agent-writable")


def _check_trusted_path(path: Path, *, executable=False) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("gate path must be absolute")
    # lstat rejects symlinks at every level, including a replaceable parent.
    for parent in reversed(path.parents):
        _check_metadata(parent.lstat(), parent, directory=True)
    _check_metadata(path.lstat(), path, directory=False)
    if executable and not os.access(path, os.X_OK):
        raise ValueError("gate is not executable")


def _read_trusted_config(path: Path) -> dict:
    _check_trusted_path(path)
    # Read once from a no-follow fd; verify the opened inode too. Trusted ancestry
    # prevents replacement by the agent between validation and open/exec.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        _check_metadata(os.fstat(stream.fileno()), path, directory=False)
        return json.load(stream)


def gate_config() -> Optional[dict]:
    try:
        config = _read_trusted_config(TRUSTED_CONFIG)
        command = config.get("command") if isinstance(config, dict) else None
        if not isinstance(command, list) or len(command) != 1 or not all(
                isinstance(part, str) and part and "\x00" not in part for part in command):
            return None
        timeout = config.get("timeout", DEFAULT_TIMEOUT)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            return None
        _check_trusted_path(Path(command[0]), executable=True)
        return {"command": command, "timeout": timeout}
    except (OSError, ValueError, TypeError):
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
               timeout=config.get("timeout") or DEFAULT_TIMEOUT, check=False,
               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, cwd="/")
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
    is_critical = critical(**call)
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


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _entry_name(value: Any) -> str:
    name = str(value or "").strip().lower()
    return name.removeprefix("mcp__")


def _mentions_ha(value: Any) -> bool:
    return TOOL in str(value).lower()


def _classify_entry(entry: Any, run, config) -> Optional[str]:
    if not isinstance(entry, dict):
        return NO_GATE.format(what="unparseable Home Assistant wrapper") if _mentions_ha(entry) else None
    name = _entry_name(entry.get("name"))
    arguments = entry.get("arguments")
    if not name:
        # Mirror core's two misplaced-name envelopes without importing core.
        inner = _decode(arguments)
        if isinstance(inner, dict) and _entry_name(inner.get("name")) == TOOL:
            name = _entry_name(inner["name"])
            rest = {key: value for key, value in inner.items() if key != "name"}
            arguments = rest.get("arguments") if set(rest) <= {"arguments"} else rest
    if name == TOOL:
        return classify(TOOL, arguments, run, config)
    if not name and _mentions_ha(entry):
        return NO_GATE.format(what="unparseable Home Assistant wrapper")
    # Explicit other tool names are not HA calls, even when their arguments
    # discuss ha_call_service (e.g. a kanban_complete summary).
    return None


def classify(tool_name: str, args: Any, run=subprocess.run, config=None) -> Optional[str]:
    if tool_name == TOOL:
        args = _decode(args)
        if _call_of(args) is None:
            return NO_GATE.format(what="unparseable Home Assistant call")
        return decide(args, run, config)
    if tool_name in WRAPPERS:
        decoded = _decode(args)
        if not isinstance(decoded, dict):
            return NO_GATE.format(what="unparseable Home Assistant wrapper") if _mentions_ha(args) else None
        # Preserve checking legacy+batch together; do not treat the batch
        # envelope itself as a nameless entry merely mentioning its children.
        calls = [decoded] if "calls" not in decoded or "name" in decoded or "arguments" in decoded else []
        raw_calls = decoded.get("calls")
        batch = _decode(raw_calls)
        if isinstance(batch, list):
            calls.extend(batch)
        elif isinstance(batch, dict):
            # Retain the prior JSON-single-object refusal for monotonicity.
            if isinstance(raw_calls, str) and _mentions_ha(raw_calls):
                return NO_GATE.format(what="unparseable Home Assistant wrapper")
            calls.append(batch)
        elif isinstance(raw_calls, str) and _mentions_ha(raw_calls):
            return NO_GATE.format(what="unparseable Home Assistant wrapper")
        for call in calls:
            why = _classify_entry(call, run, config)
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
        # An unexpected classifier/config error must not bypass a batched HA call.
        return {"action": "block", "message": NO_GATE.format(what="Home Assistant call")}
    if not why:
        return None
    logger.info("ha-critical-gate blocked %s", tool_name)
    return {"action": "block", "message": why}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)

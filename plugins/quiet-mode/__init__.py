"""Quiet mode ("movie mode") — stop every Hermes session on this Mac from touching the screen or speakers.

While quiet mode is on, a ``pre_tool_call`` hook in every Hermes process (desktop, CLI, gateway, cron,
subagents, all profiles) vetoes tools that open windows, drive the GUI, or make sound. The agent gets a
block message telling it to skip that step and keep doing non-disruptive work (or end its turn).

State lives in one host-wide file, ``~/.hermes/quiet-mode.json`` (deliberately NOT profile-scoped), so
one toggle covers every profile. It is re-read on every tool call (cheap stat), so on/off takes effect
immediately in sessions that are already running.

Toggle:  /quiet [on [90m|2h]|off|status]   (alias /movie)  ·  hermes quiet ...  ·  ~/.hermes/bin/hermes-quiet ...
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_PATH = Path(os.environ.get("HERMES_QUIET_MODE_FILE") or (Path.home() / ".hermes" / "quiet-mode.json"))

# Tools that are disruptive by nature (screen, windows, focus, speakers).
BLOCK_TOOLS = {
    "computer_use",
    # desktop_ui: panes, previews, tours, tips, layouts all move/pop UI
    "desktop_preview", "drive_preview", "annotate_preview", "gui_tour", "show_tip",
    "focus_pane", "apply_layout", "close_terminal", "desktop_project",
    # audio
    "text_to_speech",
    # opens a terminal window on screen
    "run_in_user_terminal",
    # raises the agent's Chrome window to the front
    "browser_focus",
    # browser: config uses a local, real-profile (visible) Chrome
    "browser_exec", "browser_cdp", "browser_dialog", "browser_vault_fill", "browser_vault_enter_code",
    "browser_vault_save_login", "browser_vault_unlock",
}
BLOCK_PREFIXES = (
    "spotify",                 # spotify_* plugin tools
    "mcp__chrome_devtools__",  # drives local Chrome
    "mcp__chrome-devtools__",
    "browser_",                # any other browser_* tool
)
# Read-only browser vault listing is harmless.
ALLOW_TOOLS = {"browser_vault_list"}

# Home Assistant domains that hit TVs, speakers, or the room lighting mid-movie.
HA_BLOCK_DOMAINS = {"media_player", "tts", "remote", "assist_satellite", "notify", "light", "scene",
                    "siren"}

# Shell commands that open windows / take focus / play sound.
_SHELL_PATTERNS = [
    r"(^|[;&|(`\s'\"])open\s", r"[\"']open[\"']\s*,",  # macOS `open` (shell or subprocess list)
    r"\bosascript\b", r"(^|[;&|(`\s'\"])say\s", r"[\"']say[\"']\s*,", r"\bafplay\b", r"\bplay\s+-", r"\bffplay\b", r"\bmpv\b", r"\bvlc\b",
    r"\bcliclick\b", r"\bpeekaboo\b", r"\bhammerspoon\b|\bhs\s+-c\b", r"\bshortcuts\s+run\b",
    r"\bscreencapture\b.*\s-[a-zA-Z]*i", r"\bkillall\s+(Dock|Finder|SystemUIServer)\b",
    r"\bhermes\s+(desktop|gui)\b", r"\btmux\s+attach\b",
    r"(^|[;&|(\s])(spotify|openhue)\s", r"\bblueutil\b", r"\bSwitchAudioSource\b",
    r"\bterminal-notifier\b", r"\bdisplayplacer\b", r"\bbrightness\b",
    r"--headed\b", r"\bplaywright\b.*\b(open|codegen)\b",
]
SHELL_RE = re.compile("|".join(_SHELL_PATTERNS))


# ---------------------------------------------------------------- state

def read_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def active_state() -> Optional[dict]:
    st = read_state()
    if not st.get("on"):
        return None
    until = st.get("until")
    if until and time.time() >= float(until):
        return None
    return st


def write_state(on: bool, until: Optional[float] = None, reason: str = "") -> dict:
    st = {"on": bool(on), "until": until, "reason": reason, "set_at": time.time()} if on else {"on": False}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    os.replace(tmp, STATE_PATH)
    return st


def parse_duration(text: str) -> Optional[float]:
    """'90', '90m', '2h', '1h30m', '45s' -> seconds. Bare number = minutes."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text) * 60
    total, matched = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|m|min|mins|minutes?|s|sec|secs|seconds?)", text):
        matched = True
        n = float(num)
        total += n * 3600 if unit.startswith("h") else n * 60 if unit.startswith("m") else n
    return total if matched else None


def describe(st: Optional[dict] = None) -> str:
    st = active_state() if st is None else st
    if not st:
        return "🔊 Quiet mode is OFF — Hermes may use the screen and speakers."
    until = st.get("until")
    if until:
        left = max(0, int(float(until) - time.time()))
        h, rem = divmod(left // 60, 60)
        when = time.strftime("%-I:%M %p", time.localtime(float(until)))
        tail = f" until {when} ({h}h{rem:02d}m left)" if h else f" until {when} ({rem}m left)"
    else:
        tail = " until you turn it off"
    return (f"🤫 Quiet mode is ON{tail}. All Hermes sessions on this Mac are blocked from opening windows, "
            f"driving the GUI/browser, or making sound.")


def handle(raw: str) -> str:
    """Shared by /quiet, `hermes quiet`, and the bin script. Returns a status line."""
    parts = (raw or "").strip().split()
    verb = parts[0].lower() if parts else "toggle"
    rest = " ".join(parts[1:])
    # `/quiet 2h` == `/quiet on 2h`
    if verb not in {"on", "off", "status", "toggle", "start", "stop"} and parse_duration(verb) is not None:
        verb, rest = "on", " ".join(parts)
    if verb == "toggle":
        verb = "off" if active_state() else "on"
    if verb in {"on", "start"}:
        secs = parse_duration(rest)
        st = write_state(True, until=(time.time() + secs) if secs else None, reason=rest)
        return describe(st)
    if verb in {"off", "stop"}:
        write_state(False)
        return "🔊 Quiet mode is OFF — Hermes may use the screen and speakers again."
    if verb == "status":
        return describe()
    return "Usage: /quiet [on [90m|2h] | off | status]   (no args toggles)"


# ---------------------------------------------------------------- hook

def classify(tool_name: str, args: Any) -> Optional[str]:
    """Return a short reason if this call would disturb the screen/speakers, else None."""
    name = tool_name or ""
    args = args if isinstance(args, dict) else {}
    if name in ALLOW_TOOLS:
        return None
    if name in BLOCK_TOOLS or name.startswith(BLOCK_PREFIXES):
        return f"`{name}` uses the screen, browser, or speakers"
    if name == "ha_call_service":
        domain = str(args.get("domain") or "").lower()
        service = str(args.get("service") or "")
        if "." in service and not domain:
            domain = service.split(".", 1)[0].lower()
        if domain in HA_BLOCK_DOMAINS:
            return f"Home Assistant `{domain}` would change TVs/speakers/lights"
        return None
    if name in {"terminal", "execute_code", "process_manage", "process"}:
        blob = " ".join(str(args.get(k) or "") for k in ("command", "code", "input", "data"))
        m = SHELL_RE.search(blob)
        if m:
            return f"`{m.group(0).strip()}` in a shell command can open windows or play sound"
        return None
    if name == "mcp__tool_call" or name == "tool_call":
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"))
                if why:
                    return why
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        st = active_state()
        if not st:
            return None
        why = classify(tool_name, args)
        if not why:
            return None
    except Exception:  # never let this plugin break a tool call
        logger.debug("quiet-mode classify failed", exc_info=True)
        return None
    logger.info("quiet-mode blocked %s: %s", tool_name, why)
    return {
        "action": "block",
        "message": (
            f"QUIET MODE — blocked: {why}. The user is watching a movie and has paused every Hermes "
            f"action that touches the screen or speakers ({describe(st).split('. ')[0].replace('🤫 ', '')}). "
            "Do NOT retry this or work around it (no alternative tool, shell command, or script that opens "
            "windows, drives the GUI/browser, changes lights/TVs, or makes sound). Continue with silent, "
            "background work only (files, code, headless/HTTP fetches, analysis). If the task needs this "
            "step, stop and end your turn with a short note of what is pending until quiet mode is off."
        ),
    }


# ---------------------------------------------------------------- CLI

def _cli_setup(parser) -> None:
    parser.add_argument("action", nargs="?", default="toggle", help="on | off | status | toggle | <duration>")
    parser.add_argument("duration", nargs="*", help="e.g. 2h, 90m, 1h30m (with 'on')")


def _cli_handler(ns) -> int:
    print(handle(" ".join([ns.action, *(ns.duration or [])])))
    return 0


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    for cmd in ("quiet", "movie"):
        ctx.register_command(cmd, handle,
                             description="Movie mode: block every Hermes session from using the screen/speakers",
                             args_hint="[on [2h]|off|status]")
    try:
        ctx.register_cli_command("quiet", "Quiet/movie mode: block Hermes from the screen and speakers",
                                 _cli_setup, _cli_handler)
    except Exception:
        logger.debug("quiet-mode: CLI command registration failed", exc_info=True)

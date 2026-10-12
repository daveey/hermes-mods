---
name: quiet-mode
description: "Use when user wants Hermes off screen/speakers (movie)."
version: 1.0.0
author: daveey (local)
metadata:
  hermes:
    tags: [quiet, movie, dnd, screen, speakers, focus]
---

# Quiet / movie mode

Plugin `~/.hermes/plugins/quiet-mode` (enabled, synced via daveey/hermes). While on, a `pre_tool_call` hook in
EVERY Hermes process on this Mac (all sessions, profiles, cron, subagents) blocks tools that touch the screen or
speakers. State: `~/.hermes/quiet-mode.json` (host-wide, re-read on every tool call → instant effect).

## Toggle ("movie mode", "I'm watching something", "stop using my screen")

- In chat: `/quiet` (toggle), `/quiet on 2h`, `/quiet off`, `/quiet status` — alias `/movie`.
- Shell (fast, no Hermes startup): `~/.hermes/bin/hermes-quiet on 2h|off|status`. Also `hermes quiet ...`.
- As the agent: run `~/.hermes/bin/hermes-quiet on <dur>` with the terminal tool (not itself blocked).
- Bare number = minutes; `2h`, `90m`, `1h30m` work. No duration = until turned off.

## What gets blocked

computer_use; desktop_ui tools (desktop_preview, drive_preview, gui_tour, show_tip, focus_pane, apply_layout, …);
text_to_speech; all browser_* (browser is a local visible Chrome) except browser_vault_list; chrome-devtools MCP;
spotify_*; ha_call_service for media_player/tts/remote/assist_satellite/notify/light/scene/siren; and
terminal/execute_code/process commands matching `open`, `osascript`, `say`, `afplay`, cliclick, peekaboo,
`shortcuts run`, `hermes desktop`, spotify/openhue CLIs, etc. (regex list `_SHELL_PATTERNS` in the plugin).

## If a tool result says "QUIET MODE — blocked"

Don't retry or work around it with another tool/script. Keep doing silent work; if the step is required, end
the turn with a note of what is pending. Never turn quiet mode off yourself unless the user asks.

## Pitfalls

- A newly enabled/edited plugin loads only in NEW Hermes processes; the running Desktop backend needs an app
  restart. The state file, by contrast, is live.
- Not covered: the Desktop app's own completion beep/notifications (app-side), and processes not spawned by Hermes.
- Extend coverage by editing `BLOCK_TOOLS` / `HA_BLOCK_DOMAINS` / `_SHELL_PATTERNS`, then run
  `~/.hermes/hermes-agent/venv/bin/python -m pytest -q ~/.hermes/plugins/quiet-mode/tests_quiet.py`.

# remote-screen

See and drive another Mac's screen over macOS **Screen Sharing (VNC)**, with no window opening on the Mac
Hermes runs on. Built for one job above all: **inspecting permission prompts** ("“X” would like to access …")
that block work on a headless or remote Mac. Permission inspection is **read-only**: OCR is
untrusted screen content, not proof of a real system dialog.

| Tool | What it does |
|---|---|
| `remote_screen_hosts` | Configured hosts: Screen Sharing reachable?, login stored?, the one-time setup command, and a `::screenshare` button directive for the user. |
| `remote_screenshot` | Grabs the remote screen. The image goes straight to the model; the full-res PNG is saved. `ocr: true` adds on-screen text with boxes. |
| `remote_input` | `click` / `double_click` / `right_click` / `move` / `scroll` / `type` / `key` (`"cmd+space return"`). Target by explicit coordinates in the last screenshot's image. `target_text` is refused, even with x/y supplied. Returns a fresh screenshot. |
| `remote_approve_dialog` | Compatibility name for **inspection only**: screenshot → OCR → report a candidate whose requester exactly matches the allowlist. Never clicks, even with `dry_run: false`. `status: candidate` is not an approval or authenticated UI. Candidate payloads and logs contain text only, never request/button boxes or target coordinates. Returns `inspection_only: true`, a screenshot and a JSON log in `<hermes home>/cache/remote-screen/<host>/approvals/`; asks the user to inspect/approve the real dialog. |

## Why VNC

Synthetic local clicks (CGEvent, Accessibility, `cliclick`, cua-driver) are ignored by many macOS security
prompts. Clicks sent through Screen Sharing arrive via `screensharingd` as a remote user's real input, which
is how a human approves prompts from another Mac. Verified on macOS 26: both an Automation prompt (“X” wants
access to control “Finder”) and a Files & Folders prompt (“X” would like to access files in your Desktop
folder) were approved this way, and TCC recorded them as allowed. Apple says some approvals refuse remote
input (kernel/system extensions). These historical protocol observations do not authenticate the UI;
automatic OCR-driven approval has been removed. Explicit coordinate input remains a general control tool.

## Setup

1. On each target Mac: **System Settings → General → Sharing → Screen Sharing** on, your account allowed.
2. On the controlling Mac, write `<hermes home>/remote-screen.yaml`:

   ```yaml
   hosts:
     studio:
       label: "Studio Mac"
       address: studio.local        # what this Mac can reach; host:port works too
       aliases: [studio]
       user: me                     # macOS account on the target
       # password_env: STUDIO_PW    # optional: read the password from an env var instead of Keychain
       # screenshare_address: ...   # optional: address for the user's ::screenshare button
   approve:
     apps: [Hermes Python.app, ghostty]   # exact requester names for inspection; empty = no candidates
     buttons: [Allow, OK, Always Allow, Allow Full Access, Allow While Using App]
   ```

3. Store the login in the controlling Mac's Keychain (you type it; the agent never sees it).
   `remote_screen_hosts` prints this exact command per host; the agent runs it with `run_in_user_terminal`:

   ```sh
   security add-generic-password -U -s hermes-remote-screen -a 'me@studio.local' -l 'Hermes remote-screen: studio.local' -w
   ```

Requirements: controller on macOS (Vision OCR via a small Swift helper), `cryptography` and `Pillow` in the Hermes venv (both ship with
Hermes). Target: any Mac with Screen Sharing (Apple auth types 30 and 33; classic VNC password as a fallback).

OCR uses `/usr/bin/swiftc`, never PATH lookup. Each use checks `ocr.swift` against `OCR_SOURCE_SHA256`,
compiles a snapshot into a fresh private temporary directory under the cache, binds the output's SHA-256
in process memory and rechecks it immediately before execution. Cleanup runs on success and failure.
Persistent binaries, mtimes and writable digest sidecars are not trusted. Source edits require updating
the reviewed digest. One build is shared by the full-screen scan and zooms within an inspection call,
with the binary digest rechecked before every execution. Per-call compilation costs latency; it avoids trusting a previous build or mutable
manifest. This integrity check is not a sandbox against a malicious process with the same OS UID.

## Safety

- The diagnostic allowlist matches the complete **requester name** (casefold and whitespace only),
  including punctuation and `.app` suffixes. `Terminal` does not match `Not Terminal Helper` or `Terminal.app`.
  `app` filtering is exact too. `Open` is no longer a default candidate button.
- OCR cannot distinguish a dialog from a webpage drawing the same text or bind a button to a system window.
  Therefore no OCR path selects or clicks input targets. `remote_approve_dialog` always inspects only.
  Restoring automatic approval requires an independently authenticated UI source, not tighter geometry.
- `remote_input` remains full remote control by explicit coordinates/keys; the inspection allowlist is not
  an authorization gate on that tool. A model can still choose coordinates based on what it sees; this plugin
  does not prevent model-level prompt injection or attest that coordinates are a safe permission approval.
- Password, Touch ID and admin prompts are never handled; the agent must not type passwords via `remote_input`.
- Quiet mode (movie mode): input aimed at **this** Mac's own screen is refused while it is on. Remote Macs
  are unaffected, since they don't touch the screen you're watching.
- The target Mac shows macOS's "Your screen is being controlled" indicator while a tool call is connected,
  and anyone at that Mac sees the pointer move.

## Local Screen Sharing guard

`screen_guard.py` (hooks `pre_tool_call` + `pre_llm_call`, registered even when the tools are not) refuses terminal /
execute_code (also inside `tool_call`) commands that open Screen Sharing on this Mac: `open ... vnc://`,
`open -a "Screen Sharing"`, `open -b com.apple.ScreenSharing`, `osascript ... Screen Sharing`. A control-mode window
steals key focus, hides the cursor and captures Cmd-Tab. The error points to `remote_screenshot` / `remote_input` /
`remote_approve_dialog` or a `::screenshare{host=...}` button. Allowed only when the current user turn of an
interactive chat explicitly asks ("open Screen Sharing to ..."); Kanban workers, cron and delegated subagents are
always refused. Off switch: `HERMES_SCREEN_SHARE_GUARD=0`. Tests: `tests_screen_guard.py`.

## Tests

```sh
cd plugins/remote-screen
uv run --with pytest --with cryptography --with pillow python -m pytest -q -p no:cacheprovider tests_remote_screen.py tests_screen_guard.py tests/
```

An in-process fake Apple VNC server covers type-30 login (DH + AES credential blocks), bad-password errors,
raw + zlib framebuffer decoding, pointer/key events and key combos. The approval logic is tested on OCR
fixtures: allowlisted approve, non-allowlisted refusal, background text not counting as the requester,
wrapped app names, a prompt wrapped mid-phrase (real macOS 26 OCR), password prompts, and no prompt at all.

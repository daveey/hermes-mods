# Finite gateway chats wait through update refusals

Patches: **2856, 2902**.

## Behavior

Finite gateway chats can wait through a core-update owner replacement rather than immediately lose an unadmitted prompt. Running one-shots also retry raw WebSocket handshake rejections during redial and announce their bounded wait on stderr.

## How it works

- **2856:** `_redial` catches `InvalidHandshake` subclasses alongside connection failures, keeping the 600-second per-hang-up window and five-hang-up limit. A normal output-mode one-shot gets a wait notice. The same patch adds hook unit regressions for pending Stop/steer precedence and watcher cleanup.
- **2902:** `UpdateWait` shares one 600-second pre-admission window across attach and submit. It retries ensure’s draining/starting verdicts, and `runtime_draining`/`runtime_retired` RPC refusals. Only after such a refusal do ordinary refused/timed-out/half-started dials become retryable. `_attach_through_update` closes rejected attempts. `_submit_finite` resumes the same session on the next owner before retrying the refused prompt. Stream-json suppresses the stderr notice.

## Code

- `hermes_cli/gateway_chat_view.py`
- `hermes_cli/gateway_chat.py`
- `hermes_cli/gateway_update_wait.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_gateway_chat_view_redial.py` checks HTTP-upgrade/handshake failures followed by a successful resumed receipt. `test_gateway_chat_update_wait.py` drives `run_gateway_chat`/finite view attach and submit, refusal controls, bounded waits and quiet announcement behavior. `test_update_pause_hook_mid_tool.py` checks pending steer/Stop and stops the installed watcher after normal and raising exits.

- `tests/gateway/test_update_pause_hook_mid_tool.py`
- `tests/hermes_cli/test_gateway_chat_view_redial.py`
- `tests/hermes_cli/test_gateway_chat_update_wait.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

Retries cover refusals that admitted nothing, not arbitrary failed submissions. Interactive chats, unrelated policy errors and a dial failure before any update refusal keep their previous exit. These tests use simulated gateway clients, not an end-to-end live update.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

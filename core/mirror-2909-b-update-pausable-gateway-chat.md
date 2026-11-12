# Core update: preserve gateway-hosted oneshot/lane chat turns

Patches: 2836.

- **2836** (`e055b76f94`, core update: preserve gateway-hosted oneshot/lane chat turns (park finite local turns, mid-tool cancel, one-shot redial, update_pausable))

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- Gateway chat units now publish `update_pausable` (true only for a turn under the installed pause hook), so the updater pauses them instead of waiting for a gap. A watcher cancels a long running tool call through the agent's mid-tool cancel once a requested pause has waited `MID_TOOL_GRACE_S`, the call result becoming the stamped interrupted text and the turn parking at the next boundary, then auto-continuing; a log line announces the gateway auto-continue.
- One-shot chat views redial the replacement owner after a hang-up (`REDIAL_WINDOW_S` 600 s, `REDIAL_RETRY_S` 2 s, `REDIAL_LIMIT` 5) and print the continued answer; without a dialer or past the window the old unknown-exit stays; a refusal that is not a hang-up is not redialed.

## How it works

- `gateway/session_update_pause_hook.py` (`mid_tool_cancel_step`, `start_mid_tool_cancel`, `update_pausable`), `gateway/run_shutdown_restart_wait.py`, `gateway/session_update_pause.py`, `gateway/session_update_pause_ingress.py`, `hermes_cli/gateway_chat.py`, `hermes_cli/gateway_chat_view.py`.

## Code

- `gateway/run_shutdown_restart_wait.py`
- `gateway/session_update_pause.py`
- `gateway/session_update_pause_hook.py`
- `gateway/session_update_pause_ingress.py`
- `hermes_cli/gateway_chat.py`
- `hermes_cli/gateway_chat_view.py`

## Tests

- `tests/gateway/test_update_pausable_active_work.py` (only a hooked turn is pausable), `test_update_pause_composed.py` (real agent parks and resumes without tool replay; finite local turn inside a long tool call cancelled, parked, continued), `test_update_pause_ingress.py`, `tests/hermes_cli/test_gateway_chat_view_redial.py` (redial prints continued answer; no dialer/window keeps unknown exit; non-hang-up refusal).

Test files touched by these patches:

- `tests/gateway/test_update_pausable_active_work.py`
- `tests/gateway/test_update_pause_composed.py`
- `tests/gateway/test_update_pause_ingress.py`
- `tests/hermes_cli/test_gateway_chat_view_redial.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Builds on durable update-pause checkpoints from earlier patches. Cancelling a tool mid-call is destructive to that call's own result; it is replaced by the stamped interrupted text.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.

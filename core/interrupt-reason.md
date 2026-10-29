# Every interrupt records why it stopped a turn

Patches: 0630, 0634 (daveey/hermes `7b9f2606cc`, `246370c63c`).

## Behavior

Every TUI/Desktop interrupt producer passes a fixed reason to the agent: `user_stop` (session.interrupt, default; the agent's tool_reason stays "explicit stop requested"), `ws_orphan` (WS-orphan reaper), `backend_exit` (`_stop_turns_before_exit` on SIGTERM/atexit), `lease_takeover` (takeover, two sites), `session_close` (isolated-turn close settle). Non-user reasons ride the compute-host interrupt frame (`reason` key, only when not `user_stop`) to the isolated child.

An interrupt during a model wait (`handle_api_interrupt`, budget-summary path) or a retry/recovery backoff (`abort_turn_on_interrupt`, 0634) records `{reason, phase: model_wait|retry_wait, waited_s, prompt_tokens (rough estimate)}` on `display_metadata.interrupt` of the interrupted row and `result["interrupt"]`, plus one INFO line `turn interrupted: reason=... phase=... waited=...s prompt_tokens=... model=... session=...`. Visible text is unchanged. When the interrupted call is the first of a turn (tail = user) no row is persisted; only `result["interrupt"]` and the log line carry it. Consumer: hermes-mods coach backstop `interrupt_triage`.

## How it works

`_interrupt_session_turn(reason=)` hands the reason to `agent.interrupt`; `record_api_interrupt` stamps the row (partial text, hidden placeholder or closing sentinel); `_stamp_interrupt_attribution` in the finalizer copies it to the result. 0634 extends the same record to `abort_turn_on_interrupt` before `clear_interrupt` drops the reason and adds the `phase` field.

## Code

- `agent/interrupt_control.py`, `agent/turn_api_call.py`, `agent/turn_finalizer.py`, `agent/turn_recovery.py`, `agent/message_sanitization.py` (`close_interrupted_tool_sequence(display_metadata=)`), `tui_gateway/session_lifecycle.py`, `session_reaper.py`, `methods_session_takeover.py`, `host_supervisor.py`, `compute_host.py`.

## Tests

- `tests/tui_gateway/test_interrupt_reason.py` drives a real AIAgent through a fake slow provider: session.interrupt (user_stop), `_stop_turns_before_exit` (backend_exit, model_wait), ws-orphan, lease takeover, backend exit during retry backoff (retry_wait), and the compute-host frame plus child handler. `test_tui_gateway_server.py::test_ws_orphan_reap_interrupts_isolated_turn_then_reaps` asserts ws_orphan on the frame. `test_turn_api_call_interrupt.py` pins the partial row shape.
- Untested: the `session_close` path has no dedicated test.

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- `prompt_tokens` is a rough estimate, not provider-counted.

## Plugin?

No. It changes core code that runs before or outside plugin loading.

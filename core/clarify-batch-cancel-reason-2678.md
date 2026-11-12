# Batch clarify results name why they were cancelled

Patches: **2594**.

## Behavior

- A batch `clarify` result that did not end in `submitted` now carries a `reason` label next to `outcome`. Before, a Stop, a session takeover, a backend exit and an empty client response all stored only `outcome=cancelled`, so an incident could not be attributed without transcript forensics.
- Reasons: `interrupted` (turn Stop), `taken_over` (writer-lease takeover), `shutdown` (backend exit, or process-wide clear), `session_closed` (session close), `timeout` (with outcome `timed_out`), `client_empty` (a cancel-all frame with no answers), `client_error` (an error frame or malformed answers), `no_session` (no live session to ask).
- The `clarify` tool result JSON includes `reason` only when the outcome is not `submitted`. A `submitted` result never carries one.
- `reason` is a label only. No answer content rides it; locked answers still travel in the answers.

## How it works

- `tools/clarify_batch.settle_batch(locked, frame_result, *, ended, reason)` adds `reason` for an `ended` wait (the caller's lifecycle reason) and for frame results that are cancel-all (`client_empty`) or errors (`client_error`).
- Both registries pass it: the gateway's `session_server_requests._ask` passes the wait's end reason, and the TUI gateway's `server_requests.send` (timeout) and `server_requests.cancel(..., reason)` do the same. `gateway/session_clarify_batch.ask_clarify_batch` forwards a non-submitted reason from the registry result and sets its own for no-session, interrupted-versus-timeout and bad-answer cases.
- `_clear_pending(sid, reason=None)` accepts a reason, and `_interrupt_session_turn` maps `lease_takeover` to `taken_over`, `session_close` to `session_closed` and `backend_exit` to `shutdown`, so those no longer read as a plain `interrupted`.
- `tools/clarify_tool._result` copies the label into the stored tool result.

## Code

`tools/clarify_batch.py`, `tools/clarify_tool.py`, `gateway/session_clarify_batch.py`, `gateway/session_server_requests.py`, `tui_gateway/server.py`, `tui_gateway/server_requests.py`, `tui_gateway/session_lifecycle.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_clarify_batch_settlement_parity.py`, `tests/gateway/test_clarify_lock_keeps_locked_answers.py`, `tests/tui_gateway/test_protocol.py`, `tests/tui_gateway/test_session_takeover.py`. Entry path: `settle_batch` directly; both registries (`server_requests.send`/`cancel`, and the gateway `_ask` through `ask_clarify_batch` over a fake peer) for timeout, interrupt, cancel-all and error frames; `server._interrupt_session_turn` for the `taken_over`, `shutdown`, `session_closed` and default labels; `clarify_tool` for the stored result with and without a reason.

**Untested**: the `no_session` and `client_error` paths of `ask_clarify_batch` on their own, a real Desktop or Ink client, and any consumer reading the new field.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Consumers that compared the whole result dict for equality must tolerate the new `reason` key.
- Single-question (non-batch) clarify and sudo/secret requests are unchanged: they have no question ids and no reason.
- `reason` for a plain `cancelled` without a known cause is simply absent.

## Plugin

Core change; no plugin required.

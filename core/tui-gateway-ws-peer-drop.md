# A peer dropped mid-send ends the WS read loop as a disconnect

Patch: 0602 (daveey/hermes `ead8bb5d23`).

## Behavior

When a send to a vanished WebSocket peer fails (or this side closes on the send deadline or fanout overflow), the read loop used to log `ERROR ws receive failed` with a traceback and `reason=receive_failed`: a fake crash in errors.log (self-heal desktop-2d2f8e30ac). Now the loop ends as `reason=send_failed` or `server_closed(<cause>)` with one WARNING line. Read errors while our side still holds the socket keep the ERROR.

## How it works

Starlette flips `application_state` to DISCONNECTED when a send fails or the server closes, so the next `receive_text` raises `RuntimeError('... call "accept" first')`. `WSTransport` records why it latched closed (`send_failed`, `send_deadline`, `fanout_overflow`). Before each read and when a read fails, `handle_ws` checks that record and ends the loop with the matching reason, counting send failures once (a failed dispatcher reply keeps its own reason).

## Code

- `tui_gateway/ws.py` (WSTransport close cause, `handle_ws` read loop).

## Tests

- `tests/tui_gateway/test_tui_gateway_ws_real_socket.py` drives `handle_ws` over a real starlette WebSocket (the FakeWS suites never model `application_state`): send failure then read, failure under a pending read, overflow close, failed response counted once, unexpected read error keeps ERROR.

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- Tests must use a real starlette socket; a fake that ignores `application_state` cannot reproduce the receive RuntimeError.

## Plugin?

No. It changes core code that runs before or outside plugin loading.

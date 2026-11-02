# Platform connect deadline logs the blocked await chain

Patches: 1690

- `patches/1690-daveey-Preserve-safe-await-chains-at-platform-connec.patch`

What you see: when a messaging platform's connect times out at gateway start or reconnect, `gateway.run` logs one
WARNING `Platform connect deadline: platform=<p> waits={...}` before the existing `TimeoutError`. It names where the
connect task, and the adapter's separate bot task if it has one, were suspended (DNS, TLS, HTTP or a ready-event wait),
as `file:line:qualname` entries only.

## Behavior

- `GatewayAdapterLifecycleMixin._connect_adapter_bounded` (used for initial connects and reconnects) logs the warning
  when `_wait_or_detach` reports the deadline passed, then raises `TimeoutError("<platform> connect timed out after
  <n>s")` as before.
- `waits` is a dict: `connect` = the await chain of the connect task; `bot` = the chain of `adapter._bot_task` when it
  is an `asyncio.Task` (Discord's client runs as a separate task, so the connect waiter alone shows only
  `Event.wait`).
- Each chain entry is `<file basename>:<line>:<co_qualname>`. No `repr`, source lines, arguments or locals are read,
  so tokens passed to the handshake are not logged. Chains stop at 24 frames.
- A connect that succeeds within the deadline logs nothing new.

## How it works

`gateway/connect_diagnostics.py` (new):

- `task_wait_chain(task)` starts at `task.get_coro()` and follows `cr_await` (coroutines) or `gi_yieldfrom`
  (generators), recording each frame's code location. `Task.get_stack()` would show only the outer suspended frame.
  Non-tasks return `[]`.
- `connect_waits(adapter, task)` returns `{"connect": ..., "bot": ...}`.

`gateway/run_adapters.py` calls it right after the deadline, with no `await` in between, so the suspended frames still
exist when they are read.

## Code

`gateway/connect_diagnostics.py` (new), `gateway/run_adapters.py`.

## Tests

`tests/gateway/test_connect_timeout_diagnostics.py` (new):

- `test_deadline_records_connect_and_bot_wait_without_secrets` (parametrized `initial` true/false, i.e. initial
  connect and reconnect): a fake adapter starts a bot task `stalled_handshake("DO-NOT-LOG-TOKEN")` and blocks. The log
  contains `Adapter.connect`, `stalled_handshake` and `Event.wait`, never the token, and `TimeoutError` matches
  `discord connect timed out`.
- `test_success_has_no_timeout_diagnostic`: a connect that returns `True` logs no deadline line.

The mirror pass did not rerun these tests.

Untested: a real Discord/Telegram/Slack adapter stalled in DNS or TLS (the tests use plain `asyncio.Event` waits); the
generator (`gi_yieldfrom`) branch and the 24-frame cap.

## Applying

Needs `GatewayAdapterLifecycleMixin._connect_adapter_bounded` / `_wait_or_detach` in `gateway/run_adapters.py`.
Otherwise standalone.

## Gotchas

- File basenames and qualified names can still reveal which library or code path was in use; treat the line as local
  diagnostic output.
- Only awaits reachable from the connect task and `adapter._bot_task` are shown; other tasks an adapter spawns are not.

## Plugin?

No. The connect deadline lives inside the gateway's adapter lifecycle; plugins have no hook at that point.

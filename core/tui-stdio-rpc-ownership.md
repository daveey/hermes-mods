# TUI gateway: JSON-RPC frames never reach a log-sink stdout

Patches: 1683 1684 1685

- `patches/1683-fix-tui-keep-protocol-frames-out-of-gateway-stdout-l.patch`: the fix.
- `patches/1684-test-tui-extract-stdio-fixtures-within-file-health-c.patch`: test-only; moves the stdio capture fixture
  and one broken-pipe test to keep test files under their size caps.
- `patches/1685-test-tui-declare-takeover-harness-stdout-RPC-ownersh.patch`: test-only; the takeover subprocess harness
  declares that its stdout is the RPC channel.

What you see: tool results, server requests and responses that have no live client no longer appear as raw
`{"jsonrpc": ...}` lines in gateway / Desktop logs. The stdio TUI (`tui_gateway.entry`), where stdout really is the
JSON-RPC channel, is unchanged.

## Behavior

- The module-level stdio transport (`tui_gateway.server._stdio_transport`) writes only when
  `server._stdio_is_rpc_channel` is true. That flag already existed (default `False`) and is set to `True` only by the
  stdio entrypoint (`tui_gateway.entry`). In `hermes serve` / dashboard / Desktop-embedded processes stdout is captured
  into a log file, so a frame that falls through to stdio is dropped and the write returns `False`.
- The check lives in `StdioTransport.write`, before serialization. It applies whichever way a frame reaches the stdio
  sink: `write_json`'s fallback, a session record whose `transport` is the stdio transport, or a context-bound stdio
  transport (`bind_transport`). Session and lifecycle code that holds the sink directly cannot bypass it.
- Ordinary logger output (`server.logger.error(...)`) still reaches the log.
- With the flag true, or with a real non-stdio transport bound by context or session, frames are delivered as before.

## How it works

- `tui_gateway/transport.py`: `StdioTransport.__init__` takes keyword `enabled: Callable[[], bool]` (default always
  true). `write()` returns `False` when `enabled()` is false. Other transports and other `StdioTransport` users keep the
  default.
- `tui_gateway/server.py`: `_stdio_transport = StdioTransport(lambda: _real_stdout, _stdout_lock, enabled=lambda:
  _stdio_is_rpc_channel)`. The lambda reads the module global at write time, so the entrypoint and tests can flip it
  after import. The `write_json` docstring now says that step (3) goes to module stdio only when the stdio entrypoint
  owns stdout.
- 1684 moves the `capture` fixture to `tests/tui_gateway/protocol_helpers.py` (`test_protocol.py` re-exports it) and
  moves `test_write_json_returns_false_on_broken_pipe` into `test_rpc_log_privacy.py`.
- 1685 sets `server._stdio_is_rpc_channel = True` at import in `tests/tui_gateway/test_session_takeover_e2e.py`, whose
  subprocess harness reads stdout as RPC, like `tui_gateway.entry._serve()`.

## Code

`tui_gateway/transport.py`, `tui_gateway/server.py`; tests in `tests/tui_gateway/test_rpc_log_privacy.py` (new),
`tests/tui_gateway/protocol_helpers.py` (new, 1684), `tests/tui_gateway/test_protocol.py`,
`tests/tui_gateway/test_tui_gateway_server.py`, `tests/tui_gateway/test_session_takeover_e2e.py`.

## Tests

`tests/tui_gateway/test_rpc_log_privacy.py`:

- `test_unrouted_rpc_never_reaches_gateway_log`, parametrized by frame kind (`event` via `_emit`, `request` and
  `response` via `write_json`) × route (`fallback`, `session` whose transport is the stdio sink, `context`-bound stdio).
  With the flag false, stdout points at a real log file that also has a `FileHandler` attached. The private payload marker and
  `jsonrpc` never appear, a real `logger.error` line does, and the write returns `False`.
- `test_tool_completion_callback_respects_stdout_ownership` (flag false/true × tool name `terminal` /
  `connectors__example__lookup`): the real `_on_tool_complete` callback writes the result frame only when stdout is
  the RPC channel.
- `test_explicit_rpc_channels_still_deliver` (`stdio` with flag true, `context`, `session` with a real
  `StdioTransport`): frames are delivered and nothing reaches the log stream.
- `test_write_json_returns_false_on_broken_pipe` (moved by 1684): a broken pipe with the flag true still returns `False`.
- Existing tests that write to stdio (`test_write_json_serializes_concurrent_writes`,
  `test_write_json_unserializable_payload_becomes_error_frame`, the `capture` fixture in `test_protocol.py`) now set the
  flag explicitly.

The mirror pass did not rerun these tests.

Untested here: a live Desktop or `hermes serve` process with its real log capture (the tests use a file standing in
for captured stdout), and an end-to-end stdio TUI run through `tui_gateway.entry` (only its flag-setting line is
relied on; 1685's harness declares the same ownership).

## Applying

Needs the earlier `_stdio_is_rpc_channel` flag in `tui_gateway/server.py` (set by `tui_gateway/entry.py`). Apply 1683,
1684, 1685 in order; 1684 expects 1683's test file, and 1685 is needed for the takeover e2e test to keep passing after
1683.

## Gotchas

- A new process that really uses stdout for JSON-RPC must set `server._stdio_is_rpc_channel = True` itself. Without it,
  every stdio frame is silently dropped (writes return `False`, which callers treat as "peer gone").
- A test that captures stdio frames must set the flag (use `protocol_helpers.capture`).

## Plugin?

No. The fallback transport and its ownership flag are internal to the TUI gateway; plugins cannot wrap
`StdioTransport.write`.

# Test-fixture race hardening

Mirror entries 4959, 4966, 4967, 4968, 4971. Test-only changes; no production behavior, retries or timeouts change.

## Behavior

Five flaky fixtures are made deterministic:

- **4959** process-resource observations: the bridge barrier replies before closing its server socket, so global `/proc/self/fd`
  equality could see that socket. The real server send is gated and only the unique transferred file's descriptors are inspected (all five rejected copies must be closed).
  Wrapper-child argv is snapshotted once (a double read could see full then empty argv during exec); vanished children are tolerated, AccessDenied is not.
- **4966** stack-cap fixture: the liveness watchdog handle is kept and stopped/joined before the protective patch is undone (a mocked hard exit returns, so the watchdog could outlive the patch).
- **4967** delegation: the durable failure row precedes queue publication, so the stall completion event is consumed and asserted and its runner joined before the next test.
- **4968** pause fixture: the fake tool is held on an Event until request publication or user-stop completes, released in `finally`, instead of a fixed 0.3 s sleep.
- **4971** checkout fixture: wait for the owned child's READY ack before classifying, since Linux argv could still be empty right after `Popen`; close the pipe after kill/reap.

## How it works

Each fix replaces a timing assumption with an explicit handshake (Event, READY line, join) and adds a regression that holds the racy step open
(held send-before-close, held publication, empty argv until READY). Source claims say the regressions fail on the old fixture.

## Code

None in production. Tests: `tests/test_gateway_service_child_pids.py`, `tests/tools/test_origin_cli_bridge.py`, `tests/gateway/test_loop_dump_thread_cap.py`,
`tests/tools/test_async_delegation_failed_surface.py`, `tests/gateway/test_turn_update_pause_real_agent.py`, `tests/gateway/test_update_pause_pending_followup.py`, `tests/test_update_checkout_scope.py`.

## Tests

Those files are the change. Source commits report passing runs; none were executed by this mirror.

## Applying

Apply only through the full segmented recipe in the core mods [README](README.md) ("Applying"), never as independent patches.

## Gotchas

- These fixtures depend on the production helpers existing at the matching upstream state; on a different base, re-derive the handshakes rather than copying line-for-line.

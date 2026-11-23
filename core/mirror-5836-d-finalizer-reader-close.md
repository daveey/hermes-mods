# Nonblocking SessionDB finalizer reader cleanup

Source patch ordinals: 5760–5763.

## Behavior

`SessionDB.__del__` never waits for the SQLite live-reader registry lock. Explicit closes outside finalizer scope retain normal locking. Failed deferred closes preserve live-path tracking.

## How it works

A bound-at-import `finalizer_close` context manager sets thread-local finalizer scope and restores its previous value, including nesting. Tracked close attempts acquire `_live_lock` nonblocking only in this scope; if unavailable the connection is queued for the next registry operation. A successful acquisition stays held through close, avoiding a release/reacquire race. Deferred close success clears tracked path state; failure does not falsely mark the path closed. CI change classification includes the safe-reader import surface.

## Code

- `hermes_state.py`
- `hermes_cli/sqlite_safe_read.py`
- `scripts/ci/classify_changes.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_state/test_finalizer_live_lock.py` covers another thread holding the lock, ordinary blocking close, interpreter exit with a daemon lock owner and retention of the successful nonblocking acquisition. `test_finalizer_live_lock_guards.py` covers thread/scope isolation, failed cross-thread closes and nested scope restoration.

## Applying

Apply the SessionDB finalizer integration with the safe-reader implementation. No schema or configuration change is needed.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Deferral avoids finalizer lock waits; it does not promise immediate physical connection closure. A failed deferred close is not certified as success. Finalizer state must remain thread-local and restore the enclosing state.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.

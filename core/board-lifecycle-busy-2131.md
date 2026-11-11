# Retryable board busy responses and sampled lock provenance

Mirror ordinals: **2118–2119**.

## Behavior

When bounded board lifecycle reads exhaust their retries and have no usable memo, HTTP callers receive `503` with `Retry-After: 1` and `{"detail":"Kanban board is busy; retry shortly."}`. The application-wide typed exception handler also covers in-process plugin routers. Existing usable stale memo results can still return successfully; unrelated database errors are not mislabeled as retryable lifecycle contention.

Busy fallback logging adds the exception type, SQLite code, process ID and sampled lifecycle-lock holder metadata when available. It is diagnostic evidence about a timed-out wait, not a definitive historical explanation for every busy read.

## How it works

`install_board_busy_handler` registers a FastAPI handler for `BoardLifecycleBusy` on the shared web application. Storage remains HTTP-independent and still applies its bounded retry/memo behavior before that exception crosses the boundary.

The board lifecycle's raw reentrant locks become `LifecycleLock` wrappers. The wrapper tracks outermost owner thread and acquisition time while preserving recursion depth. Failed waits attach elapsed wait, sampling timestamp and holder snapshot to the typed busy exception. Only timeout sampling obtains a symbolic stack, capped at sixteen basename/line/function frames; no SQL, row content, locals, source reads or logging occurs under the board lock. If ownership changes while sampling, the snapshot is discarded rather than attributing an old stack to a new owner.

Both board-read and session-task memo fallbacks include the captured provenance. Ordinary SQLite busy errors can have empty holder details; diagnostics do not invent a holder for them.

## Code

- `hermes_cli/web_board_busy.py`, `web_server.py`: shared HTTP handler and installation.
- `hermes_cli/kanban_generation.py`: diagnostic lock use, timed access and typed busy exception.
- `hermes_cli/kanban_lifecycle_diagnostics.py`: reentrant owner accounting and bounded snapshots.
- `hermes_cli/kanban_session_activity_busy.py`: fallback logging for both memo paths.

## Tests

Source regressions were inspected, **not executed here**.

`tests/hermes_cli/test_web_board_busy.py` exercises representative core/plugin route behavior: cold busy returns the exact retryable 503, warm memo returns stale rows with 200, and a non-busy database error stays 500 without a retry header.

`tests/hermes_cli/test_kanban_busy_provenance.py` holds locks from another thread, including the raw close path, and asserts captured PID/thread/wait/held/stack metadata without waiting for release. It checks logging through board-read and session-task fallbacks for lifecycle and ordinary SQLite busy errors, rejected release accounting, and ownership-change snapshot suppression. These tests do not establish production contention frequency.

## Applying

Use [README.md](README.md)'s complete snapshot replay. Restart the web backend to install the shared exception handler and lock wrapper. Run both listed Python test modules in an isolated source checkout with FastAPI test dependencies. Clients should honor the retryable response without treating a busy board as an empty board or corrupt store. No live route was invoked for this doc.

## Gotchas

- The handler is specific to `BoardLifecycleBusy`, not every `OperationalError`.
- A good stale memo remains useful; 503 is the exhausted-read boundary, not a replacement for caching.
- Holder snapshots are best effort and process-local. A holder can finish at the sampling instant, leaving empty metadata.
- Logs retain the existing board path; stack sampling avoids source/locals but is not a universal redaction layer.

## Could this be a plugin?

A plugin route benefits automatically from the application's handler and can choose client retry UX. The shared exception mapping and lifecycle-lock instrumentation belong in core so plugin and built-in routes share semantics without duplicating storage policy.

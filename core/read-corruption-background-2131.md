# Unconfirmed read corruption: pending checks, not a write latch

Mirror ordinals: **2121–2122**.

## Behavior

A read that encounters structural corruption and cannot finish its foreground confirmation within the budget still fails with its original exception. It is not silently replayed or returned as empty data. For confirmation `timeout` or `no_budget`, however, it does not latch unrelated writers as corrupt: it schedules an isolated background whole-file check and tags the exception `hermes_read_corrupt_unconfirmed`.

While that file's check is queued or running, subsequent corruption handling fails the read without spawning another foreground confirmation. Confirmed background damage latches storage. A clean result or diagnostic failure does not latch it. Immediate foreground damage/check errors and a replay recurrence retain their existing fail-closed behavior. Writer behavior is unchanged, including timeout latching and the rule that COMMIT is never replayed.

Web read boundaries return a pending-check 503 (`Session read failed; background integrity check pending.`) instead of relatching the tagged error or telling the caller to repair an unconfirmed store. A real canonical-store latch asynchronously creates or updates an agent-owned investigation card through independent Kanban storage, not Inbox.

## How it works

The pooled-reader handler checks existing latch/cache-release conditions before the new pending/timeout path. The tag travels with the original exception; `note_storage_error` declines to latch tagged read errors, including at outer profile/session handlers.

A process-wide background worker serializes whole-file checks. Real paths coalesce same-file requests, while distinct profile files queue with their captured context; completion removes each pending entry. The subprocess opens read-only with query-only enabled, starts isolated Python (`-I -S`), closes inherited descriptors and has no execution deadline for this background mode. POSIX starts it in a separate session, but a parent watcher exits it if its originating parent disappears, avoiding an orphaned long read snapshot. This is process-local coalescing, not distributed singleflight.

`ConfirmResult` can carry per-table diagnostic results. The incident reporter only schedules for the current context's canonical `state.db`, captures profile/board context before threading, and serializes reporting with a file lock. It reuses an open card for that canonical path, appending another owner's first error; resolved/archived cards do not absorb new incidents. A separate isolated per-table quick check appends diagnostic results. Reporter failure cannot replace the original storage failure.

## Code

- `hermes_state.py`: tagged read failures, pending coalescing and timeout/no-budget deferral.
- `hermes_state_corruption_confirm.py`: background queue, isolated child lifetime, result validation and per-table output.
- `hermes_state_health.py`: tagged-error exclusion and latch incident scheduling.
- `hermes_state_corruption_incident.py`: canonical-store Kanban investigation/reporting.
- `hermes_cli/web_routers/sessions.py`, `_common.py`: pending 503 mapping across read boundaries; 2122 extends consistent reporting.

## Tests

Source tests were inspected, **not executed in this doc task**.

- `tests/hermes_state/test_storage_corruption_background.py`: timeout/no-budget fails reads without latching, one same-file background check, writes remain available, immediate/background damage latches, distinct profile checks queue, diagnostic errors do not latch, and outer session/profile handlers do not relatch. Additional usage/models/resolve/browse cases assert pending 503 without repair messaging.
- `test_storage_corruption_scoped_confirm.py`: the read timeout expectation now explicitly excludes replay/latch and asserts scheduling.
- `test_storage_corruption_incident.py`: first-error scheduling, open-card reuse/new card after closure, per-table results, scratch-store exclusion, isolated child results, parent-disappearance exit and real damaged-table diagnostics preserving healthy-table output.

These source tests include subprocess and route helper boundaries, not fresh live-service or production-file proof here.

## Applying

Use the complete snapshot replay in [README.md](README.md). Restart affected backend processes to load the handler and worker changes. Run the listed storage tests with the supported Python and test dependencies in a disposable source tree. Do not corrupt, recover or replace a live database to validate this doc. No live storage incident or card was created for documentation.

## Gotchas

- Pending is not healthy proof and not confirmed damage. The failed read remains failed.
- Only background `damaged` latches; do not convert child startup/parsing failures into damage in this new path.
- An unbounded background check can be expensive and queue later files; it is read-only, not free.
- This does not relax writer safety or existing already-latched storage behavior.
- A later clean diagnostic cannot prove the file was never damaged or automatically clear an established latch.
- Independent Kanban reporting may fail; it is diagnostic, not part of committing session data or recovering storage.

## Could this be a plugin?

Incident presentation could be extended by a plugin. The read exception tag, replay/latch decision, subprocess isolation and outer-handler semantics belong in core storage; a plugin reacting after a latch would be too late to keep an unconfirmed read from disabling writes.

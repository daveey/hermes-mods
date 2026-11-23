# Subscription-specific notification revisions

Source patch ordinals: 5564–5565.

## Behavior

Notification signature reads can reuse subscription rows across unrelated board-content changes, without trusting incomplete trigger coverage or private fallback connections. Missing optional revision support falls back to literal reads.

## How it works

`kanban_notify_revision` owns a narrow counter and exact trigger definitions for subscription identity, cursor and `inflight_from` columns. Writable initialization repairs coverage atomically; readers verify schema and counter inside the same subscription snapshot. Cache keys include file identity/schema generation. Only retained shared-reader custody publishes reusable memos; private fallback reads cannot displace them. The optional counter is excluded from required schema parity.

## Code

- `hermes_cli/kanban_notify_revision.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_reader_lifecycle.py`
- `tui_gateway/notification_idle.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/tui_gateway/test_notify_revision.py` covers unrelated content, each read-column change, private fallback, partial/foreign schema, atomic repair, snapshot races, new connections, replaced databases/WAL generations, missing counters and install failures. `test_notify_subs_gate.py` retains subscription/inflight gates; `tests/hermes_cli/test_kanban_schema_owner.py` verifies optional-counter absence is not required-schema drift.

## Applying

Apply writer installation and read-side revision checks together. Existing boards may use literal reads until a writable upgraded open installs coverage. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Readers never repair schema. A global annotation revision is not a substitute for subscription-only coverage. Reusing a previous numeric counter after a missing-row repair requires a changed schema generation to prevent stale memo acceptance.

## plugin?

No. This is gateway notification and SQLite reader infrastructure.

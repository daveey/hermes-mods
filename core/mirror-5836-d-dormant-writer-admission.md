# Dormant monotone Board writer admission

Source patch ordinals: 5747–5748.

## Behavior

Canonical Board connections register `replay_writer_revision()` at revision 1. Installing this code does not activate a gate. A separately authorized activation can persist a monotone minimum and refuse unsupported or lower-revision DML writers while preserving reads.

## How it works

`register(conn)` installs a connection-local SQL function. `activate(conn)` requires an existing transaction and uses an inner savepoint to install `replay_writer_gate1` and `replay_writer1_*` INSERT/UPDATE/DELETE guards on every existing non-SQLite table. Independent monotonicity triggers prevent minimum downgrade, replacement or deletion. Its naming keeps it separate from removable `kanban_sync_%` triggers.

## Code

- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_sync_writer_gate.py`
- `docs/designs/board-replay-writer-admission.md`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_sync_writer_gate.py` covers inactive compatibility, pre-opened and cached legacy statements, all three DML guards, lower revisions and cold reopen, detach/reinstall, monotonicity, outer rollback and real DDL-installation failures, including errors caught by an outer caller. The follow-up relocates the suite into the CLI test directory.

## Applying

Apply without calling `activate`. Production activation is not supplied by this slice: it requires separate receiver capability, complete provenance implementation at a higher revision, old-writer and in-flight-send drain, and an authorized schema-owner boundary.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Revision 1 means admission support only, not original-capture certification. Existing tables alone are guarded; later tables need installation in their own schema transaction. Deliberate DDL or forged SQL functions are outside this DML fence, and database guards cannot retract network bytes already in memory.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.

# Terminal non-success archive of contained tasks

Source patch ordinals: 5556–5563.

## Behavior

Ordinary archive can terminally close contained work only after every frozen authority is archived, or done without a nominated producer. The target must be unclaimed triage with no human request or pending deletion. Closure is not release: the marker and dependency edges remain, and dependents remain unsatisfied.

## How it works

Local archive requires exact immutable stop attestation and writes `containment_closed` receipts atomically under a connection/task/transaction-scoped archived-only permit. Dependency readers map containment archives to an unsatisfied status while ordinary archived parents retain their semantics. Enrolled boards require `terminal_archive: 1`, send the canonical `archive` operation with durable `client_op_id` and bounded `archive_metadata`, then authenticate exact stop/closure, archived audit and task readbacks before local projection. Unknown replies reuse the request; ordinary sync projection does not prevent settling a pending archive once. Settled requests do not replay comments. Closure receipts participate in replica certification and quarantine/rebuild paths.

## Code

- `hermes_cli/kanban_containment_terminal.py`
- `hermes_cli/kanban_containment_terminal_remote.py`
- `hermes_cli/kanban_containment_canonical.py`
- `hermes_cli/kanban_containment_guard.py`
- `hermes_cli/kanban_containment_lifecycle.py`
- `hermes_cli/kanban_containment_replica.py`
- `hermes_cli/kanban_db_archive.py`
- `hermes_cli/kanban_db_parent_states.py`
- `hermes_cli/kanban_creator_archive.py`
- `hermes_cli/kanban_db.py`
- `website/docs/user-guide/features/kanban-containment.md`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

The source `tests/hermes_cli/test_kanban_containment_terminal_*.py` suites cover authority policy, all-episode atomicity/rollback, exact stop and closure authentication, permit scope, local custody, dependency mirrors, canonical capability/metadata/readback refusals, replica ordering, quarantine/rebuild, request recovery and sync-before-retry settlement. The `pending_op` suite ensures non-archive requests cannot replay an archive. `tests/tools/test_kanban_owner_wake_terminal_archive.py` checks terminal archives cannot reopen the authority. Event-GC coverage retains closed archive history without stopping unrelated cleanup.

## Applying

Apply this whole lifecycle/sync family in source order with the preceding containment implementation. Remote use also requires a Board implementation advertising the terminal archive capability. The source client alone cannot supply server-side atomic admission. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Do not use `archive --rm`: unlink/delete stay fenced. Old stops lacking durable attestation are not authenticated by upgrade alone. Generic local preconditions cannot be transported atomically; only the recognized `CreatorScope` is supported remotely. No automatic dependent-owner notification or replacement producer is supplied. A displayed archived status is not successful dependency completion.

## plugin?

No. The SQL guards, lifecycle reducers, canonical delivery and dependency semantics must remain core-owned.

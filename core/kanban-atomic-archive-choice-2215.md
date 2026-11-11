# Atomic archive choices and evidence

Patches **2160–2161**.

## Behavior

Core `archive_task` accepts optional `precondition` and `comment` arguments alongside `event_extra`. A refusal leaves no archive transition or reason comment. Success commits the choice comment, archive state and event receipt together before workspace cleanup. A transition exception rolls them back together; a repeated archive does not add a duplicate comment.

## How it works

`kanban_db_archive.archive_task` enters the core `write_txn` and `reason_comment` contexts, runs the precondition inside that transaction, then retains the comment only after recording the archive. A nonempty precondition return refuses the operation. Worker termination and workspace cleanup remain post-commit. 2161 replaces the forwarding facade function with a tail import re-export, preserving `kanban_db.archive_task` without another wrapper.

## Code

`hermes_cli/kanban_db_archive.py`, `hermes_cli/kanban_db.py`.

## Tests

**Upstream-supplied coverage, not executed in this mirror documentation pass.** `tests/hermes_cli/test_kanban_archive_choice_fence.py` adds:

- `test_archive_precondition_refusal_writes_nothing`: precondition runs in a transaction; no status, event, comment or cleanup effect on refusal.
- `test_archive_commits_choice_comment_and_receipt_before_cleanup`: cleanup observes committed state and comment; event contains the receipt; repeated archive leaves one comment.
- `test_failed_archive_rolls_back_comment_state_and_receipt`: injected archive-event failure rolls back state and comment.

These are direct core DB tests; no plugin, UI or HTTP projection is exercised by this test file. 2161 supplies no new tests.

## Applying

Use the complete ordered replay in [README.md](README.md), including its replay plan and bridges. The patches are not independent units.

## Gotchas

Callers should supply a precondition instead of nesting a write transaction around the archive call. Post-commit cleanup is intentionally not atomic with the choice: a cleanup failure does not undo the durable archive receipt.

## Plugin?

The decision UI can be a plugin, but atomic evidence and state transitions belong in core. This patch does not itself ship that UI.

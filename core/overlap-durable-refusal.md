# Durable overlap refusal explanations

Patch 1371; source `551588c6f57659c0781dbc054a44b7e7325dda33`.

## Behavior

Overlap refusals retain each match explanation in board events. All-remote matches are recorded in a bounded JSONL log rather than disappearing when no local board can hold the event.

## How it works

record includes why capped at 300 characters; all-remote records append under kanban home logs/create_overlap.jsonl, rotating one generation at 1 MiB.

## Code

hermes_cli/kanban_overlap.py

## Tests

Source regression inventory: tests/tools/test_kanban_create_overlap.py: test_tool_local_refusal_event_carries_why, test_tool_all_remote_refusal_is_recorded_durably, corresponding cli/rest cases, and test_why_is_bounded_and_the_log_rotates.

These runtime suites were not rerun in this mirror pass. Executed mirror inventory/replay/tree checks are recorded in [mirror-1375-verification.md](mirror-1375-verification.md); they do not certify runtime behavior.

## Applying

Apply the complete snapshot using the [README replay recipe](README.md#applying), not this patch independently.

## Gotchas

The durable log is a local detection record, not a remote board write. Concurrent log rotation is not certified by these tests.

## Plugin?

This patch mirrors the source implementation; no separate public mod installation is required.

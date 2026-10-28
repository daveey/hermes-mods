# Overlapping Kanban session-list annotation reads

Patch 0661 (`b549dbc49c`).

## Behavior

Overlapping session-list polls share one annotation refresh instead of occupying a worker for every identical read. A caller arriving during a refresh takes the last good snapshot; a caller arriving after completion reads again. Failed reads remain unknown, not stale success.

## How it works

A single refresher coalesces the annotation count, waiting-source and attention reads. It does not introduce a time-based cache that prevents a later non-overlapping poll from refreshing.

## Code

`hermes_cli/kanban_session_activity.py`.

## Tests

Canonical `scripts/run_tests.sh tests/hermes_cli/test_kanban_session_activity.py`: 51 passed on Linux during this mirror run. The source commit adds overlap and failure-path regression coverage. No live Desktop load measurement is claimed here.

## Applying

Apply after 0660, before 0662, in curated series order.

## Gotchas

A last-good snapshot is only for overlapping requests; a failed refresh must not be reported as a fresh successful read.

## plugin?

No; the shared core session-list annotation reader.

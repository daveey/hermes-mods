# Transactional per-session change cursor

Mirror ordinals: 3992.

## Behavior

Adds changed_since for state.db session-list inputs so readers can distinguish changed session IDs from counter-only writes; this is infrastructure, not a replacement of every list consumer.

## How it works

Writer init installs two empty tables, a revision index and generated sessions triggers without backfill. Every column except explicit pure counters is watched; message_count stamps only zero/nonzero crossings. Rekeys stamp both IDs. A reader takes one snapshot and trusts only exact trigger coverage, revision/MAX invariant, nonregression, file/shm/schema/epoch identity and unchanged local-lineage receipt digest. Otherwise it returns FULL, optionally with a fresh cursor. The lineage digest uses the existing partial index rather than adding a hot state_meta trigger. Compression stamping switches total_changes to UPDATE rowcounts to exclude trigger side writes.

## Code

- `hermes_state_compression.py`
- `hermes_state_schema.py`
- `hermes_state_session_change.py`
- `scripts/benchmark_session_change_overhead.py`

## Tests

- `tests/hermes_state/test_session_change_cursor.py`: Checks empty/idempotent install, activity without messages, workspace-root backfill, display/admission edits, first-message versus later counters, continuation/lineage changes, rekey/delete/same-ID replacement, missing/stale triggers, invariant damage, in-place/row/file restores and limit-driven FULL.
- `tests/tui_gateway/test_session_projection_revision.py`: Accounts for the cursor's two trigger writes per heartbeat stamp while retaining the separate projection-counter no-write assertion.
- `tests/hermes_state/test_hermes_state.py`: Legacy-column reconciliation fixture drops the new update trigger before schema surgery.
- `tests/hermes_state/test_runtime_schema.py`: Legacy runtime-schema fixture likewise removes the column-referencing trigger before dropping columns.
- `tests/hermes_state/test_session_git_metadata_generation.py`: Legacy git-metadata fixture removes that trigger before column removal.
- `tests/hermes_state/test_bounded_recent_sessions.py`: Legacy activity-column fixture removes that trigger before column removal.
- `tests/hermes_cli/test_browse_schema_drift.py`: Older-store fixtures remove the trigger before simulated schema drift.
- `tests/hermes_cli/test_cli_sessions_behind_newer_surfaces.py`: Read-only legacy created-source fixture removes the trigger before dropping the column.
- `tests/hermes_cli/test_sessions_repair_declarative.py`: Repair fixture removes the trigger before legacy runtime-column removal.
- `tests/hermes_cli/test_sessions_source_local.py`: Legacy read-only source fixture removes the trigger before schema surgery.
- `tests/agent/test_insights.py`: Legacy insights fixture removes the trigger before dropping created_source.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Messages are not covered: message-time ranking still needs its own watermark. Installation fails open on SQLite errors and readers remain FULL until repaired. The supplied benchmark gates paired wall overhead with an A/A control, not all process CPU; it was not run here.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

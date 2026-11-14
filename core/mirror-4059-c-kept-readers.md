# Reuse background read handles with generation-safe retirement

Mirrored entries: 3856.

## Behavior

Background Kanban triage reads reuse one query-only connection per thread and board file, and remote-notice session checks reuse a read-only SessionDB per process/path. The notice write itself still borrows the shared writer. Replaced files, WAL-generation changes and invalid handles cause retirement/reopen rather than reuse of stale storage.

## How it works

`kanban_kept_reader.reading` opens through the normal configured connect path, enables query_only, bounds each thread’s retained boards, and validates file identity, shm generation, initialization and poison/registration state on every borrow. Nested borrows and fenced/read-only-schema boards get ordinary per-call handles. Recovery may evict idle readers across threads; borrowed handles refuse retirement and force retry. SQLite errors or leaked transactions discard a reader, and exited-thread handles drain on a later borrow.

`hermes_state_kept_reader.read_only` retains a refcounted read-only SessionDB keyed by resolved path with db/sidecar identity checks. Retired borrowers remain usable until the last give-back. Registry teardown retires these handles too. `NoticeSessionStore` routes reads to that handle and exposes a separate notice_writer context for persistence and outcome refresh.

## Code

- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_decompose.py`
- `hermes_cli/kanban_kept_reader.py`
- `hermes_state_kept_reader.py`
- `hermes_state_registry.py`
- `tui_gateway/kanban_notice_session_store.py`
- `tui_gateway/kanban_remote_notices.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_kept_reader.py`: Asserts repeated triage calls prepare one connection, later writes remain visible, query-only rejects writes, replacement/init-state loss reopens, recovery evicts idle cross-thread handles but never closes a borrowed one, nested borrows are separate, SQLite errors discard and exited-thread handles close.
- `tests/hermes_state/test_state_kept_reader.py`: Checks reuse without schema reconciliation, notice reads without writer churn, replaced/closed/error handles reopening, delayed close while borrowed, registry teardown, and notice persistence through a writable shared writer rather than the read-only facade.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Do not write through a kept reader or hold a transaction across borrows. This changes handle lifetimes, not the corruption guard’s fd-scan algorithm; no speedup measurement is claimed by this pass. The per-call fallback paths are not equivalent to a retained handle for performance purposes.

## Plugin?

No. Generation recovery and registry teardown must own all retained database descriptors.

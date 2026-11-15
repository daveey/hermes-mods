# Commit-keyed watcher stores and subscription signatures

Mirror ordinals: 3974, 3975, 3976.

## Behavior

Reuses read-only watcher connections and subscription rows when their actual inputs are unchanged, while releasing stores for profile teardown and WAL generation changes.

## How it works

watch_store keeps a bounded 32-store tracked read-only LRU with data_version, file identity and generation keys; size/mtime changes reopen SQLite page caches for in-place restore. Sidecar identities detect stranded WAL generations. Registry close_all/close_all_under releases watcher handles; released paths stay transient until another tracked owner returns. Group/session watchers use these readers; legacy digests keep a stale key while throttled so their trailing edge still runs. Skill signatures share root walks only within one published-scopes call. Subscription reuse requires proven trigger coverage, activity revision and in-flight marks; active claims still poll each tick.

## Code

- `agent/skill_commands.py`
- `hermes_cli/kanban_activity_revision.py`
- `hermes_state_registry.py`
- `tools/skills_tool.py`
- `tui_gateway/change_watcher.py`
- `tui_gateway/notification_idle.py`
- `tui_gateway/watch_store.py`

## Tests

- `tests/agent/test_skills_scope_memo.py`: Shared roots walk once per call, shared/scoped changes move the next signature, and memoized rows equal fresh signatures.
- `tests/tui_gateway/test_watch_store_gate.py`: Asserts next-pass commit visibility without reopen, coarse-mtime correctness, replacement/removal recovery, trailing digest execution, bounded tracked connections, transaction cleanup, WAL generation reopen, selective release, transient released reads, owner-return retention and in-place restore content. Includes a synthetic signature CPU budget.
- `tests/tui_gateway/test_notify_subs_gate.py`: Bookkeeping commits avoid full subscription rereads; cursor/add/remove/in-flight/event changes are observed, uncovered triggers fall back to rereads, and claims in flight poll without commits. Includes a synthetic board-signature CPU budget.
- `tests/tui_gateway/test_change_watcher_groups.py`: A failed read preserves a known-good answer but leaves its key stale for retry; successive fallbacks can move.
- `tests/tui_gateway/test_change_watcher_sessions.py`: Counts legacy digest work rather than connection opens to retain throttle assertions under kept readers.
- `tests/tui_gateway/test_session_projection_revision.py`: Warm unchanged reads perform only data_version probes and no new open/projection read; failure fixtures reset the retained connection before injecting an open error.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Do not equate data_version across different connections: generation/file identity binds keys. Trigger coverage failure must read rows. These fixture budgets are not deployment CPU measurements; transient observers intentionally forgo retention after release.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

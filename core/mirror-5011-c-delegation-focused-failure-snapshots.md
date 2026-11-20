# Read-only focused delegation failure snapshots

Patches: **4601** (5011 snapshot).

## Behavior

Focused failure-surface polls no longer open the async ledger writer or rerun durability bootstrap. Unchanged reads and due-time do not rescan. Missing stores are not created. External commits refresh only the requested scope. RPC authorization, live children, progress, and process observations are never memoized.

## How it works

Bounded thread-local read-only handles plus commit-keyed scoped SQL snapshots. The 24h cutoff is evaluated each call, including inclusive boundary and clock rollback. Errors discard handles. Atomic file replacement never reuses old rows.

## Code

- `tools/async_delegation.py`
- `tools/async_delegation_focused_reads.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/tools/test_async_delegation_focused_reads.py`
- `tests/tui_gateway/test_subagent_list_focused_reads.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

A commit during query is not certified as current. Schema-unready stores recover without migration from polling. Fixture CPU deltas are not live whole-RPC acceptance.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

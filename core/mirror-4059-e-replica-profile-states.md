# Origin-scoped replica projection reuse

Mirror ordinals: 3985, 3986.

## Behavior

Alternating origin profiles no longer discard each other's replica projection and read memo. Slim decoded stages can be shared only when the record does not link that reading origin.

## How it works

Projection and read-memo slots include replica path, gateway and origin profile and are bounded to 16 entries. link_targets identifies eligible linked origins; whole linked records stay origin-owned, unchanged unlinked/error stages can share. Shared rows are reconciled against the replica even when old scopes were evicted, and shared maps disappear with their last state. Memo publication replaces the dictionary rather than mutating readers' snapshots.

## Code

- `hermes_cli/kanban_replica_projection.py`
- `hermes_cli/kanban_session_activity.py`

## Tests

- `tests/hermes_cli/test_kanban_replica_profile_states.py`: Alternating multi-profile reads equal full reference results; decode counters pin one changed-row decode with cross-origin reuse, unchanged per-profile hits, link movement/deletion, malformed recovery, retention caps and deleted shared-row eviction under scope turnover.
- `tests/hermes_cli/test_kanban_replica_scope_controls.py`: link_targets matches linked_sessions exactly for malformed/valid inputs; alias folds retain origin-specific sessions/freshness and whole titles; deletion removes cards; reset clears all states/memos and LRU republishing preserves recent scopes.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Clock rollback and freshness changes still invalidate read memos. Sharing a linked origin's slim stage would lose whole-record evidence. Fixture equivalence and counters do not prove live Desktop CPU reduction.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

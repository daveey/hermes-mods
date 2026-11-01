# Scope link-projection invalidation to its readers

## Behavior

Sync-shadow database writes no longer invalidate the origin-ledger link projection used by session activity annotations. Count fingerprints still include shadows, so synced counts retain their existing freshness. Local-board and origin-ledger writes still invalidate the link projection immediately. This adds no TTL and changes no writer or schema.

## How it works

`_activity_fingerprint(include_shadows=True)` keeps the existing inputs by default. `_links_fingerprint` passes `False`: its projection reads the origin ledger, hub store and local boards, not sync-shadow databases. This removes an irrelevant dependency without introducing semantic digests or additional cache state.

## Code

- `hermes_cli/kanban_session_activity.py`: fingerprint dependency split.
- Patch **1478**, source `a229655398887f15a65ab2a838f243611462a65d` (PR #492).

## Tests

`tests/hermes_cli/test_kanban_links_fingerprint_scope.py` uses real WAL stores to prove shadow bookkeeping and task writes reuse the link projection while count fingerprints change; writes to local boards and the ledger rebuild it. The regression was RED (two projections instead of one), then GREEN. Implementation verification passed 84 tests across this regression, sidebar reads, session activity, chat links, annotation snapshots and the original profile-scan serialization tests, plus all 15 check gates. Exact head `5dbe6c09844be1b374a602fec5c6b9dcf8f61cc1` passed CI run `41288605109` before normal merge.

These tests establish reader invalidation behavior, not a measured production CPU reduction. Full mirror replay is recorded in [mirror-1478-verification.md](mirror-1478-verification.md).

## Applying

Use the bridge-aware full-series recipe in [README.md](README.md), policy/replay snapshot 1478. This follows [state-keyed session scans](profiles-session-scan-cache.md), but addresses a separate annotation dependency.

## Gotchas

Counts still read shadows and must invalidate on them. Do not remove shadows globally. Real local-board or ledger changes can still rebuild links; existing time/config/alias behavior is not redesigned here.

## Plugin?

No. This narrows dependencies of a core annotation reader.

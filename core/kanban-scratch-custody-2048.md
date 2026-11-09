# Evicted scratch custody

Patches **2048–2049, 2054**. Companion to [scratch archiving](kanban-scratch-archive.md).

## Behavior

Evicting a foreign task retains a host-local receipt for its explicit, existing managed scratch directory. Custody records a path, not permission to delete it. Scheduled scratch GC can reclaim it only after fresh Board evidence says done/archived, retention has elapsed, and local safety checks pass. Receipts are neither synced ownership nor task events.

Legacy discovery requires an exact original local admission → ended Kanban session → ended-run tombstone link for this board/task/run. Ambiguous admissions, another task's tree, nested lookalikes, live sessions, admissions and ancestor turn leases prevent reclamation.

## How it works

`retain` runs inside eviction's transaction, after eligibility is rechecked. `gc_task_scratch` includes custody receipts in the owned-path inventory and invokes `gc_custody`. Authority reads happen outside write transactions; unavailable transport or HTTP 410 `Resync` stops the pass without consuming receipts. Missing paths retain evidence and skip the Board call.

GC checks terminal evidence identity, lease fields, ended runs and retention; consults existing archive, active-child, cross-board sharer and owned-job guards; then fences the unchanged receipt, absent local task, path device/inode/mtime and legacy link inside the local write transaction. It deletes the receipt before renaming aside, commits, then removes the detached tree. Rename failure rolls back; commit failure attempts restoration without overwriting a reoccupied path and stops the pass. Archive refusal preserves the tree.

Patch 2054 replaces collection-time wall clocks in the fixture with a custody-module-only clock and adds exact lease/retention boundary cases; production clocks are unchanged.

## Code

- `hermes_cli/kanban_scratch_custody.py`: receipt storage, legacy discovery, authority checks, fenced detach and restoration.
- `hermes_cli/kanban_worker_node.py`: transactional retention during eviction.
- `hermes_cli/kanban_worktree_gc.py`: custody inventory and scheduled GC entry.

## Tests

**Not rerun this pass.** `tests/hermes_cli/test_kanban_scratch_custody.py` exercises real SQLite eviction and `gc_task_scratch`/`gc_custody`, with injected Board transport: rollback, exact legacy links, retention, lease expiry, live-session/profile vetoes, sharers, archive/bare-repository refusal, pickup races, changed receipts/trees, HTTP 410, and detach/restore failures. Lease and retention boundary tests cover before/at/after expiry. This is not live Board or natural-timer verification.

## Applying

Apply 2048, 2049 and 2054 in published series order using [the core replay instructions](README.md). Existing scratch archive, Board evidence and run-tombstone infrastructure are prerequisites; these are not standalone patches.

## Gotchas

Zero/negative retention does not authorize removal. Missing or unreadable evidence fails closed. A post-detach `rmtree` failure leaves the hidden trash tree with a consumed receipt and logs its exact location; it is not reported as successful removal. A failed restoration likewise requires inspecting the logged location. The session-history reader is read-only and specifically lockguard-exempt, not an exemption for Board connections.

## Plugin eligibility

Not plugin-only: transactional eviction, ownership inventory and GC fencing must share core database/lifecycle boundaries.

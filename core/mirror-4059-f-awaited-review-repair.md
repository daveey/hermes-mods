# Exact-head repair continuation after awaited reviews

Mirror batch 6; ordinals 4047.

## Behavior

A timed owner park may resume once after an awaited exact-current-head request for changes, not just approval. This releases repair work without granting merge authority or bypassing CI.

## How it works

The latest orderly executed scheduled/continued park must have a claimed event linked by its relational run FK. Every awaited completed claimed review must provide valid verdict, full SHA and named PR scope; the union covers every holding PR at a freshly read/re-read head. Invalid siblings cannot be outvoted. The next executed claim consumes continuation.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/kanban_awaited_review.py`
- `tests/hermes_cli/test_kanban_active_pr_awaited_review.py`
- `tests/hermes_cli/test_kanban_awaited_review_repair.py`

Mail entries in the ordered series:

- 4047: [`53a1d6b1b7162644b6660af739497206bee8b88f`](patches/4047-fix-kanban-resume-timed-review-waits-for-exact-head-repair-daveey.patch)

## Tests

- `tests/hermes_cli/test_kanban_awaited_review_repair.py`: schedule/continue plus awaiting/wake-check paths resume once; repeated reads do not mutate verdict; claim/reclaim restores the hold. Stale/malformed SHA, wrong scope, unfinished/unclaimed review or owner, foreign park, crash/reclaim/timeout, missing end, unknown/moved head and uncovered PR reject. Synced run-ID remapping succeeds through event FKs.
- `tests/hermes_cli/test_kanban_active_pr_awaited_review.py`: change-request controls now distinguish stale-head refusal from current-head repair, and consume the permitted continuation before reasserting active_pr.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

A change request is never approval. Unknown current-head reads fail closed; completion before the park and generic capability/prose evidence are insufficient.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

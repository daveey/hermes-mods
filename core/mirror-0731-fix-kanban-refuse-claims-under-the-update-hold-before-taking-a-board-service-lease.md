# fix(kanban): refuse claims under the update hold before taking a Board-service lease [daveey]

Patch 0731, source `452d8e128de9d6829fd096a70cae5adc37bcec77`.

## Behavior

fix(kanban): refuse claims under the update hold before taking a Board-service lease [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `"""Kanban claim publication boundary for the POSIX updater fence."""`
- `def claim_and_open_run(conn, task_id, source_status, lock, expires, now, *, even`
- `from typing import Any, Iterable, Optional`
- `def claim_task(`
- `def claim_review_task(`
- `def test_hold_blocks_kanban_claims_without_mutation(tmp_path, monkeypatch, revie`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> During a core-update drain window the admission hold stands for minutes. claim_task/claim_review_task
> acquired the Board-service lease first, then claim_transaction refused and the lease was released, so the
> service recorded a claimed+released run per ready card per dispatcher tick (1129 junk runs across 17 cards
> in one 11-minute window on the MacBook, 2026-10-28). A lock-free early read of the hold now returns before
> the lease; claim_transaction still re-checks under admission.lock.

## Code

- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_db.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_admission_fence.py`

New or changed test definitions present in the source diff:

- `test_hold_skips_board_service_lease_before_claiming`
- `test_claim_rejects_nested_transaction_before_waiting`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

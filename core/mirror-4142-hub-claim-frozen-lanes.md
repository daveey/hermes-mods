# Frozen hub claim lanes before admission

Mirror snapshot 4142; ordinals 4091–4094.

## Behavior

- The hub's claim-time admission screen now judges cards in both the ready and the review lane. Before, only ready cards were screened, so a review card resuming from a scheduled wait could be claimed on the hub without a `hub_admission` event.
- The dispatcher takes its ready and review candidate lists once, before the screen runs. A card that an asynchronous wake publishes into either lane while the screen is running is not claimed in the current tick. It waits one tick and is then judged, moved, kept or failed open under the existing admission policy.
- Cards that already have a verdict are not judged again; the late wake still waits one tick.
- Dry runs and non-primary boards do not run the screen and keep their previous candidate lists. When review dispatch is disabled, no review lane is enumerated.
- No change to the production judge budget. The verdict-application fence from earlier work is unchanged.

## How it works

- 4091: A new `screened_lanes(conn, dry_run, review_enabled, deferred)` in `kanban_hub_admission_claim.py` reads the ready lane, reads the review lane if enabled, then (only when not a dry run and the board is primary) runs `safe_screen` and merges its deferred ids with any passed in. It returns the ready rows, review rows and deferred set with deferred ids removed. `_dispatch_once_locked` calls it in place of the separate ready/review enumeration. `worker_pickup` in `kanban_db_sync.py` now only copies foreign cards and returns an empty set; it no longer screens. `_SCREENABLE` widens from `status = 'ready'` to `status IN ('ready', 'review')`, and `_candidates` orders ready then review rows.
- 4092: Adds tests for the control flags of `screened_lanes` and for the outcomes of a review card that wakes (move, keep, judge unavailable, budget exhausted).
- 4093: Switches `lane_rows` in both `_candidates` and `screened_lanes` to the pool-aware version in `kanban_admission_pools`, so the frozen snapshot and the per-tick screen budget follow the same priority-pool order as the dispatcher.
- 4094: Test-only. The capacity-order test now runs the real screen twice (three cards judged, then one) so the review card is admitted before the Board capacity assertions, because review screening makes a fourth unjudged card wait behind the three-call admission budget.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_hub_admission_claim.py`
- `tests/hermes_cli/test_kanban_hub_claim_wake_race.py`
- `tests/hermes_cli/test_kanban_capacity_order.py`

Mail entries in the ordered series:

- 4091: [`95e0f6400a7e9cf9782fb0efeb0cfbe9f1ac16ac`](patches/4091-daveey-Freeze-hub-claim-lanes-before-admission-to-ex.patch)
- 4092: [`c59a1b20ce4a88b76dd05c08a7df9b79a358592b`](patches/4092-Exercise-frozen-lane-dispatch-controls-and-review-ad.patch)
- 4093: [`8c00e84b992cb3ea12c6dc84c07b2ada98da71f6`](patches/4093-Preserve-priority-pool-order-in-frozen-admission-lan.patch)
- 4094: [`7be97a6a376c3980f415e4c11d3a10105fef2e1a`](patches/4094-daveey-isolate-capacity-ordering-from-review-admissi.patch)

## Tests

`tests/hermes_cli/test_kanban_hub_claim_wake_race.py` (new in 4091, extended in 4092):

- `test_wake_after_screen_waits_for_next_judged_pass`: parametrized over lane (ready, review), wake kind (`wake_check_stale`, `condition_met`, `continuation_woken`) and already-judged or not. A wake delivered right after the screen leaves the card unclaimed by the hub in that tick; the next tick claims it, the `hub_admission` event precedes `claimed`, and the judge is called once for unjudged cards and not at all for judged ones.
- `test_screened_lanes_preserves_dispatch_controls`: for dry run, replica mode, primary and review-disabled combinations, asserts which lane reads and screen calls happen, the merged deferred set, and that late ids are filtered out of both lanes only when the screen ran.
- `test_review_wake_preserves_screen_outcomes`: for `move`, `keep`, `unavailable` and `budget`, asserts who holds the review card, the recorded verdict (`kept` reason, `admit` false, a `moved` event to a pool worker, or no verdict) and that a budget-limited card appears in `admission_deferred`.

`tests/hermes_cli/test_kanban_capacity_order.py` (4094): `test_capacity_fence_is_lane_local_and_survives_review_resume` pre-admits candidates through two `screen` calls (3 then 1 judged) before checking lane-local capacity and lease ordering.

The tests drive the dispatcher tick through the in-process test harness; no other entry path (CLI, REST, Desktop) is tested here and none is claimed.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The wake fence only delays a late card by one tick; it does not prevent the wake itself.
- The screen's judge budget is shared by ready and review cards now, so review cards can push other unjudged cards to a later tick (4094 adjusts a test for exactly this).
- Cards deferred before the screen (ids passed into `screened_lanes`) stay deferred.
- A failed screen still fails open as before, per the existing `safe_screen` contract.
- This document makes no deployment or live-behavior claim.

## Plugin?

No. The change is in the core dispatcher's candidate enumeration and admission ordering, which a plugin cannot reorder.

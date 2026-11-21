# Chronological run selection for operator-resume receipts

Mirror entries: 5238, 5239, 5268. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Operator-resume and core-update requeue helpers select the newest run by start time, not the largest replica-local integer ID. A valid resume receipt remains usable when synced runs receive out-of-order IDs.

## How it works

Queries order by `COALESCE(started_at, 0) DESC, id DESC`; the integer ID is only the tie-breaker. This applies both to `_latest_run` and scans that skip core-update requeue-only runs. Existing receipt matching can use portable `run_uid` on synced schemas.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_core_update_requeue.py`
- `hermes_cli/kanban_operator_resume.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source fixtures deliberately invert ID/time order, check receipt survival and `run_uid` matching on a synced schema, and directly pin newest-started selection plus skipping requeues when an older run has the largest ID.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_operator_resume_requeue.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

A null start time sorts as zero. This is a selection correction, not a migration or renumbering of runs, and it does not authorize stale receipts lacking the other required matches.

## Why core instead of a plugin

The authoritative resume/requeue queries and receipt interpretation are in core; post-processing plugin output would leave dispatch and operator actions disagreeing.

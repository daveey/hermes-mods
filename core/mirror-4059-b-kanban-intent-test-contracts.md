# Kanban queue clocks, resume receipts and overlap intent

Patches: 3792, 3794, 3811.

## Behavior

Test-only coverage protects user-visible intent: queue refresh age belongs to the last actual comment, a one-continuation receipt survives only until a real run spends it, and a moved marker alone is not mutual overlap enumeration.

## How it works

The queue fixture chains uncommented sightings and verifies their stored comment timestamp stays at the original comment, whether the comment table is retained or absent. A later flap must then produce a real refresh comment.

Operator-resume fixtures begin with `after_run = None`: no runs or core-update requeues preserve the receipt, but the first actual claimed run spends it. Only an explicit Boolean `merge_authorized = False` qualifies. The overlap fixture places a moved marker next to a one-sided enumeration and requires both `enumerated` and `own_distinct` to decline it.

## Code

Test-only changes; production code is not modified by these mails. The targeted core contracts are described above.

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_queue_reason_sighting.py` (patch 3792).
- `tests/hermes_cli/test_kanban_operator_resume_after_none.py` (patch 3794).
- `tests/tools/test_kanban_create_overlap_same_creator.py` (patch 3811).

- **3792:** Two uncommented queue sightings retain the original commented_at timestamp whether the comment table is kept or absent; the eventual refresh adds a second comment instead of merely another sighting.
- **3794:** A None-after receipt survives no runs and update-only requeues, is spent by the first real claim even after requeues, and grants nothing for missing/non-False merge_authorized values.
- **3811:** A moved marker next to one-sided enumeration returns None from both enumerated and own_distinct.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

None-after does not mean indefinitely valid. Core-update requeues are not genuine continuation use. A moved marker cannot replace the other party's matching marker; the contract is conjunctive.

## Plugin?

No. These are test contracts for core event history and overlap/continuation policy.

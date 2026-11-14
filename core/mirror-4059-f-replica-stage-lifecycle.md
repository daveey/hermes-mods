# Replica shared-stage lifecycle

Mirror batch 6; ordinals 4008.

## Behavior

Test-only coverage pins immediate cleanup of shared replica stages and reuse of malformed-row failures across profile origins.

## How it works

The fixtures exceed the projection state cap, interrupt both new and existing state advancement after a row has been staged, and retain an unrelated replica as a control. A repaired generation must recover from the cached malformed row.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/hermes_cli/test_kanban_replica_shared_lifecycle.py`

Mail entries in the ordered series:

- 4008: [`dc6fd9de4ddada7801cc4c61b5b4aa205134cfdc`](patches/4008-daveey-Pin-replica-shared-stage-cleanup-and-malformed-row-reuse.patch)

## Tests

`tests/hermes_cli/test_kanban_replica_shared_lifecycle.py`: shared keys equal live replica paths immediately after every eviction; failed advancement removes only the failing replica; the survivor needs no new decode. Repeated malformed JSON across origins decodes once, produces equal errors, then decodes the repaired generation once.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

This commit changes tests only. A cached error must not be mistaken for permanently poisoned repaired data.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

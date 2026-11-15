# Session checkpoint, thread handoff and reply-delivery tests

Mirror batch 6; ordinals 4036, 4037, 4052.

## Behavior

Test-only changes pin durable folded-input settlement, published-before-start thread handoff and exactly-once busy replies without conflating execution FIFO with transport completion order.

## How it works

Checkpoint fixtures separate an old user row at the transcript mark, a new physical-lineage row and an unrelated session. Release publishes current pending FIFO immediately and acknowledges only settled transfers once. The rewind fixture join helper waits for the published worker’s start event using the same deadline before joining. An event-gated transport deliberately sends the second reply first.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/gateway/test_fold_checkpoint_boundaries.py`
- `tests/gateway/test_shared_busy_receive_loop.py`
- `tests/tui_gateway/test_prompt_turn_join.py`
- `tests/tui_gateway/test_tui_gateway_server.py`

Mail entries in the ordered series:

- 4036: [`4136845212702ce53b6d1fdf4dc7432765385314`](patches/4036-daveey-Cover-folded-checkpoint-history-boundaries-and-pending-publication.patch)
- 4037: [`4e8245f6bf1c4a894d865908020ee41a27070d13`](patches/4037-daveey-Wait-for-prompt-worker-startup-before-joining-rewind-turns.patch)
- 4052: [`e78a5cf8f4e088d7f7fe8b7233eec0f0aa0961d3`](patches/4052-daveey-test-gateway-allow-independent-busy-reply-delivery-order.patch)

## Tests

- `tests/gateway/test_fold_checkpoint_boundaries.py`: only new user history on the physical target settles the folded admission; old/equal-mark and unrelated history stay queued despite a completed tool suffix. Pending publication, acknowledgement and transfer cleanup are exact and idempotent.
- `tests/tui_gateway/test_prompt_turn_join.py`: a real held-start worker does not trigger premature join failure; a never-started worker fails explicitly. `tests/tui_gateway/test_tui_gateway_server.py` contains the amended shared join helper.
- `tests/gateway/test_shared_busy_receive_loop.py`: both normal and reversed delivery preserve exact reply multiplicity, queued/started/terminal receipts and background-task completion; the gated case explicitly asserts second-before-first transport order.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No production ordering guarantee changes. The test helper uses a real thread start event, not broader deadlines/retries; execution FIFO does not promise ordered adapter delivery.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

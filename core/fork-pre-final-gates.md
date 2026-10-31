# Detached forks skip pre-final gates

Patch 1374; source `57b10c9efd73edb36e88a735d5ded2945ef474ca`.

## Behavior

Detached background review and side-question turns skip pre_final_gate callbacks by default, preventing fork output from entering parent-session gate state. Ordinary replies remain gated.

## How it works

eligible_callbacks filters _turn_origin or _persist_disabled through include_forks. A fork also marked background needs both include_background and include_forks. Ineligible callbacks do not trigger gate stream buffering.

## Code

agent/pre_final_gate.py

## Tests

Source regression inventory: tests/agent/test_pre_final_gate.py::test_skipped_for_fork_turns_unless_opted_in: background_review, side_question and persist_disabled, ordinary-turn control and explicit fork opt-in.

These runtime suites were not rerun in this mirror pass. Executed mirror inventory/replay/tree checks are recorded in [mirror-1375-verification.md](mirror-1375-verification.md); they do not certify runtime behavior.

## Applying

Apply the complete snapshot using the [README replay recipe](README.md#applying), not this patch independently.

## Gotchas

include_background alone is not fork consent. This is a core callback eligibility change, not a plugin replacement.

## Plugin?

This patch mirrors the source implementation; no separate public mod installation is required.

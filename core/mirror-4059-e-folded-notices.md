# Folded notices settle once across checkpoints and preserve notes

Mirror ordinals: 3972, 4003.

## Behavior

Notices already carried by a durable paused-turn user message settle once instead of being redelivered on resume. Completion results that should be display notes retain their own claim rather than being folded.

## How it works

The checkpoint receives the current generation's folded admission IDs and settles them in its own transaction. A user row after transcript_mark in the conversation chain proves folded text durable; otherwise transfers requeue in FIFO positions. Only after commit are fold/steer fences dropped and settled observers published. Fold selection shares completion_is_note with claim processing, using consume=False so scans neither spend unattended-wake allowance nor emit claim-decision logs.

## Code

- `gateway/session_completion_notes.py`
- `gateway/session_fold_pending.py`
- `gateway/session_update_pause.py`
- `hermes_state_update_pause.py`

## Tests

- `tests/gateway/test_fold_pending_admissions.py`: Across persisted-user and tool-suffix-only pause shapes, each notice appears exactly once over resumed history plus later turn text, terminal outcomes settle correctly, both fences clear and each steered observer is acked once.
- `tests/gateway/test_fold_pending_notes.py`: Requested-stop results in unviewed chats remain separate display cards with original metadata; viewed controls fold. Producer/viewer/closed-parent/wake-limit cases show repeat scans are read-only and only a qualifying own claim consumes an unattended wake.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

A tool suffix alone does not prove folded notice text persisted. A refused checkpoint rolls the settlement back; failed/interrupted turns still requeue transfers. Always-noted producers remain notes even with a viewer.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

# Compaction anchors only intent whose turn is still live

Mirrored entries: 3847.

## Behavior

After compaction, an already answered request is not reinserted as the latest actionable ask merely because a later process-completion turn has no human row. An unanswered resumed request still retains one actionable copy, and a same-turn continuation after a partial reply remains live.

## How it works

The post-engine anchor pass uses `newest_live_intent` from `context_compressor_turns`: scan backward to the newest real user row or tool-carried steer and stop there. If `turn_ended_after` says that intent’s turn ended, return no anchor rather than falling through to an even older request. The existing continuation placeholder is appended when no intent is live.

This closes the second restoration path in `conversation_compression._ensure_compressed_has_user_turn`; the earlier in-flight-task restoration was already turn-aware. Real user anchors still use fresh message copies and steer text still becomes a user anchor when live.

## Code

- `agent/context_compressor_turns.py`
- `agent/conversation_compression.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/agent/test_compaction_anchor_ended_turn.py`: Runs AIAgent._compress_context with a real SessionDB and ContextCompressor, checks both returned and persisted conversations omit an answered batch and keep the placeholder, and confirms an unanswered resumed batch has exactly one actionable copy. Helper-level cases cover process/kanban/internal triggers, ended steers and partial-reply continuations.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Testing only ContextCompressor.compress would miss the post-engine anchor pass. A summary can legitimately mention completed work as history; the test excludes summary rows when counting actionable copies. This is not a claim that all notice kinds are non-intent scaffolding.

## Plugin?

No. Both compaction restoration passes must share core turn-end semantics.

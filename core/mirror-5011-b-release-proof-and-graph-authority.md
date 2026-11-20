# Release evidence and cross-card authority

Mirror entries: 4513, 4533, 4535.

## Behavior

- **4513** — Delayed legacy comment references can be corroborated from a unique direct-tool receipt in the writer’s retained transcript.
- **4533** — A standing ask is retained when a named completed card handed work to an open successor, followed for up to three hops.
- **4535** — Replica-only parent or child endpoints produce a foreign-card diagnosis before local graph mutation.

## How it works

For unresolved legacy events lacking `comment_uid`, release checks can open the writer's local state transcript read-only. Proof requires one successful top-level receipt for the task/source integer, one declaration and reply for its call ID across all tools/messages, a direct `kanban_comment` call for the same board/task, matching author and exact body, and call/result timestamp bounds. Exactly one receiver row must match. This identifies comment data but does not acknowledge it or replace the receiver's watermark rules.

Standing-ask premise checks inspect the newest completed event's `verified_cards` and `created_cards`. Open or locally unknown successors retain the premise; closed successors can be followed transitively with a visited set and three-hop bound. Other ask invalidation policy remains unchanged.

Graph tools inspect both child and parent with the established read-only foreign-card lookup. A foreign endpoint returns ownership provenance and instructions to verify current owners before arranging a wait; it neither imports the replica nor mutates a remote graph.

## Code

- `hermes_cli/kanban_block_policy.py`
- `hermes_cli/kanban_comment_transcript_proof.py`
- `hermes_cli/kanban_release_comments.py`
- `tools/kanban_tools_graph.py`

## Tests

Source proof suites cover delayed direct receipts, ambiguous declarations/replies/bodies, competing receipts, other tools, mismatched boards, integer collisions and unchanged release watermarks. Ask tests cover verified/created successors, unknown successors, transitive handoffs and closed/no-successor controls. Graph tests cover both foreign endpoints, unchanged local linking and unknown/disabled-sync behavior. No live release or cross-gateway mutation was performed.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_ask_recheck.py`
- `tests/hermes_cli/test_kanban_comment_transcript_ambiguity.py`
- `tests/hermes_cli/test_kanban_comment_transcript_controls.py`
- `tests/hermes_cli/test_kanban_release_transcript_proof.py`
- `tests/tools/test_kanban_link_replica.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Missing/pruned transcripts, wrapped batches, shell calls, redacted bodies and ambiguous evidence remain unproved and cannot be overridden by a high --seen value. Successor traversal is bounded, not a global graph resolver. A replica's created_on field is provenance, not proof of current execution owner.

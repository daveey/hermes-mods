# Duplicate-completion idempotence and denial parsing

Patches: 3791, 3827.

## Behavior

Repeating completion of a duplicate already listed in the detector card's body adds no redundant comment. Explicit negation such as “not as duplicate of” is not accepted as a positive duplicate declaration.

## How it works

The detector query now retrieves the active detector card's body as well as its ID. Its seen check recognizes the exact newline/bullet task entry in the body or a matching earlier comment; a task ID merely quoted elsewhere does not count. The denial regex makes the judged-verb phrase optional before `as`, closing the gap for direct `not as duplicate` wording while preserving ordinary positive clauses.

## Code

- `hermes_cli/kanban_dup_detector.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_dup_detector_repeat.py` (patch 3791).
- `tests/hermes_cli/test_kanban_dup_detector.py` (patch 3827).

- **3791:** Repeated completion of the body-listed task adds no comments; a task ID only quoted in that body still gets its own completion comment.
- **3827:** New denial phrases containing not as duplicate are excluded by the existing parser parameterization, while positive and other denial cases remain in the same suite.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

The body check is deliberately a structured bullet check, not any substring containing the ID. Negation belongs to the duplicate clause: an earlier unrelated “No code changes” clause does not negate a later positive declaration.

## Plugin?

No. These are core completion-detector parsing and idempotence fixes.

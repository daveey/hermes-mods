# Earlier-run overlap and precise duplicate-language recognition

Mirrored entries: 3860, 3866, 3883.

## Behavior

The key-family exemption still permits one creator’s same-run batch, but no longer hides its own keyed cards created in an earlier run. Unknown run-start data preserves the existing exemption.

Tests pin exact duplicate-occurrence line boundaries and duplicate-language rules so incidental quoted ids, id prefixes, dotted retries and negated judgments do not become incorrect duplicate evidence.

## How it works

`kanban_overlap._find` calls `earlier_run` when an own card shares the key family. That helper parses creation/run epochs and compares strictly earlier creation; it does not remove the family exemption for known same-run batches or unrelated creators.

The repeat-detector tests distinguish the required newline-plus-dash prefix and trailing space after the full id. Further test rows exercise dotted reissue counters and the word tries, each joining word that stops a negator reaching a duplicate keyword, and each judgment verb that lets negation deny that keyword. These follow-ups do not alter production duplicate grammar.

## Code

- `hermes_cli/kanban_overlap.py`
- `hermes_cli/kanban_overlap_own.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/tools/test_kanban_create_overlap_same_creator.py`: Replays a same-family earlier-run pair and requires an overlap event/refusal, while same-run and unknown-start controls remain exempt. Adds dotted/tries counters that must not be mistaken for enumeration or distinct-leg proof.
- `tests/tools/fixtures/kanban_overlap_same_creator_replay.json`: Adds normalized replay evidence and run timestamps used by the same-creator tests; it is a fixture, not an independent test runner.
- `tests/hermes_cli/test_kanban_dup_detector_repeat.py`: Checks a mid-line dash reference and a longer id with the queried id as prefix still produce one new occurrence comment rather than suppressing it.
- `tests/hermes_cli/test_kanban_dup_detector.py`: Asserts each join word ends negator scope and every supported negated judging-verb form suppresses duplicate attribution.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Earlier-run comparison does not prove all similar cards are duplicates; the ordinary overlap verdict still decides. Unknown timestamps retain the exemption. Do not weaken occurrence delimiters to substring matching. Replay fixtures exercise create-overlap logic, not all REST or interactive entry paths.

## Plugin?

No. Shared create-overlap and duplicate evidence grammar are core task admission/accounting logic.

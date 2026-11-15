# Duplicate-language and same-creator overlap guards

Mirror ordinals: 3729, 3730, 3733, 3753, 3768, 3772.

## Behavior

- Duplicate completion parsing distinguishes a denial of duplication from “no-op as duplicate” or “never started because duplicate” prose.
- A keyword cannot reach past another duplicate/supersede keyword; keyword-only parenthetical asides are ignored without hiding a card-bearing aside.
- Same-creator overlap distinguishes staged indexed legs from reissues with attempt, revision, or try/take counters, including dotted versions and moved index markers.
- CRLF existing-work records and their list contents are stripped without removing surrounding substantive text.

## How it works

`kanban_dup_detector.sibling_named` bounds each keyword's card window at the next keyword, checks clause-local negation and a clause-ending zero/none count, and blanks keyword-only parentheses with equal-length spaces. Joining words keep a negator attached to another action, while judging verbs such as “marked as” preserve genuine denial.

`kanban_overlap_own` compares changed token spans. Counter indices are removed individually, so an attempt counter cannot hide a real fan-out index in the same span. Differing deleted/inserted markers with matching shapes also count as moved index fields. Ordinary “pass” legs remain distinct; try/take counters are reissues except staged “N of M” forms. Test-only patches isolate each reissue condition, completion-comment deduplication, newline/apostrophe behavior, one-sided replacement markers and CRLF record stripping.

## Code

- `hermes_cli/kanban_dup_detector.py`
- `hermes_cli/kanban_overlap_own.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_dup_detector.py` — Assertions pin skipped negated/zero-count keywords, bounded keyword windows, genuine duplicate references despite non-governing negatives, parenthetical handling, newline clause boundaries, never, and typographic-apostrophe contractions.
- `tests/hermes_cli/test_kanban_dup_detector_repeat.py` — Noting the same completed duplicate twice produces one comment.
- `tests/hermes_cli/test_kanban_existing_work_checked.py` — CRLF heading/label forms remove the complete existing-work list, its card/PR references and carriage returns, retaining preceding and following prose.
- `tests/tools/test_kanban_create_overlap_same_creator.py` — Parametrized title pairs distinguish retry/version counters from staged legs and moved enumeration markers; a marker on only one replaced side is not an enumeration.
- `tests/tools/test_kanban_create_overlap_same_creator_guards.py` — One failed reissue condition at a time changes reuse to refusal; shared-code-only matches do not deduplicate, explicit follow-up citations are admitted, own same-session cards remain compared, review/implementation cards remain distinct, and the tool uses its resolved assignee.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

These are bounded text heuristics, not a general natural-language parser. Similar titles alone do not authorize a new card or deduplication. Later counter/negation refinements supersede the earlier rules in this group; apply the full ordered series.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.

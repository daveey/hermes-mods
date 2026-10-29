# fix(kanban): a plain-word board slug is not a board reference [daveey]

Patch 0942, source `004623277de54b96e2761fc4552dd577a42d6163`.

## Behavior

fix(kanban): a plain-word board slug is not a board reference [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `open cards on every board of this gateway (live board DBs) and every other gatew`
- `def words(text: str) -> set[str]:`
- `def test_a_board_named_in_passing_with_an_unrelated_title_is_not_a_match(home):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> create_overlap treated any bare slug token in the title as naming that
> board. The mini ci-runners alarm title contains "runners", so every
> create exited 2 against open cards on board runners that shared one
> other word (stall.log 2026-10-28 20:42-21:28 PDT).
>
> A plain alphabetic slug now counts only via an explicit reference
> (/slug, board slug, slug board). A hyphenated slug in the title still
> counts. Tool, CLI and REST alarm creates are covered.

## Code

- `hermes_cli/kanban_overlap.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_kanban_create_overlap.py`

New or changed test definitions present in the source diff:

- `test_plain_word_board_slug_in_the_title_does_not_name_that_board`
- `test_bare_plain_word_in_the_body_does_not_name_that_board`
- `test_explicit_board_reference_still_names_the_board`
- `test_hyphenated_slug_in_the_title_still_names_the_board`
- `test_cli_alarm_create_is_not_refused_for_a_plain_word_board_slug`
- `test_rest_alarm_create_is_not_refused_for_a_plain_word_board_slug`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

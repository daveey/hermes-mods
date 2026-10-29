# fix(ci): ci-flaky-cards does not refile a job an existing card already cites [daveey]

Patch 0893, source `6db9b6d0bb22a5ba8a7bc42ffbbc7877ed16cabc`.

## Behavior

fix(ci): ci-flaky-cards does not refile a job an existing card already cites [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `files one card per test id.`
- `from __future__ import annotations`
- `class Board(Protocol):`
- `class CliBoard:`
- `def _card_body(entry: dict[str, str], run_url: str, branch: str, previous: str |`
- `def file_cards(entries: Iterable[dict[str, str]], *, run_url: str, branch: str,`
- `class FakeBoard:`
- `def test_flake_after_the_card_was_closed_opens_a_linked_new_card():`
- `def test_state_round_trips_and_rejects_unknown_report_versions(tmp_path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> file_cards only deduped against its own state file, so a flake whose job a
> CI-watch card had already fixed (t_01a1858daf93c588, job 123202653589) was
> refiled as t_01a185af6885ba83. Before opening a card it now reads the whole
> board through `hermes kanban list` (+ `show` for comments of related cards):
>
> - a card (any creator, any status) citing this job for this test id owns it:
>   comment it if open; if closed, skip, record it in state, log a skip line,
>   so a later job's flake still opens a linked new generation
> - citing = the job URL, or its GitHub run URL plus the job id
> - otherwise an open card about the test id gets the comment, not a duplicate
>
> Card: t_01a185b661b854de

## Code

- `scripts/ci/flaky_kanban.py`

## Tests

Source test files changed by this commit:

- `tests/ci/test_flaky_kanban.py`

New or changed test definitions present in the source diff:

- `test_job_url_a_done_foreign_card_already_cites_is_not_refiled`
- `test_github_run_url_plus_job_id_counts_as_citing_the_job_url`
- `test_job_url_cited_only_in_a_done_cards_comment_is_not_refiled`
- `test_job_url_cited_by_an_open_card_appends_there`
- `test_open_foreign_card_for_the_test_id_is_commented_not_duplicated`
- `test_annotations_round_trip_through_the_github_poll`
- `test_cli_board_skips_a_job_a_done_card_already_cites`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

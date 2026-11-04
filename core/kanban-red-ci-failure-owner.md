# kanban_create: one owner per red-CI failure

Patches: **1800** (`0c57293711`, `fix(kanban): kanban_create refuses a second owner of one red-CI failure (same failing test or Actions run) [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A new card naming the same failing test function, or the same Actions run when neither names a test, as an open card is refused (`create_overlap`) unless lineage, waits-on, recovers, a citation of the owner's id or `distinct_from` applies.

## How it works

`hermes_cli/kanban_overlap.py` extracts test names and run ids from title, opening paragraph and FAILED lines; cases in `tests/tools/kanban_overlap_failure_cases.json`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1800: Five cards owned test_busy_board_open_does_not_block_session_poll on 11-02 (run 41516070553) and two owned the termux assembly failure on 11-03: their titles share too few words for the word-overlap rule. A card now names failing tests (title test_ names, node ids in a failing sentence of the opening paragraph, FAILED log lines) and Actions runs; an open card naming the same test function, or the same run when neither names a test, refuses the create (create_overlap) unless lineage, waits-on, recovers, a citation of the owner's id, or distinct_from applies. t_01a19ea9f1739df6

## Code

`hermes_cli/kanban_overlap.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tools/kanban_overlap_failure_cases.json`
- `tests/tools/test_kanban_create_overlap_failure.py`: `test_1102_ci_main_watch_card_names_the_failing_test_only_with_the_new_body`, `test_1102_each_later_card_is_refused_naming_an_open_earlier_card`, `test_1103_second_card_of_the_pair_is_refused_naming_the_first`, `test_a_card_citing_the_owner_by_id_is_admitted`, `test_a_test_named_only_as_a_verification_step_is_not_a_failure`, `test_cli_refuses`, `test_closed_owner_never_matches`, `test_distinct_from_admits_and_records`, `test_exact_key_retry_is_not_a_second_card`, `test_identity_sources`, `test_real_non_duplicate_pairs_have_no_failure_match`, `test_rest_refuses` (+2 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- The public fixture's hub address is normalized to `192.0.2.10` (reviewed fingerprint in `mirror-policy-1821.json`).

## Plugin?

No: these change core modules that plugins cannot hook.

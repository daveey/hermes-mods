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


## Follow-up 1867: a ci-main-watch FAILED list keeps the same-CI-run rule

Patches: **1867** (`b9e55cc720`, `fix(kanban): a ci-main-watch FAILED list keeps the same-CI-run rule [daveey]`). Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

### Behavior

- A red-main card carrying ci-main-watch's `failed tests:` block still refuses a duplicate that names the same Actions run when the other card lists no tests, so same-run duplicates no longer get through (1867).

### How it works

`hermes_cli/kanban_overlap.py` `same_failure` falls back to the shared-run rule when only one side carries tests.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1867: ci_main_watch now writes each red run's `failed tests:` block (FAILED <node id> lines) into every red-main card. failure_identity turns those lines into `tests`, and same_failure refused a shared run only when neither card had tests, so once the owner carried the list the five same-run duplicates #732's replay refused got through again ("no match at all"): t_01a18505110a5892 and t_01a18507649f5425 against t_01a18504581b2063 (run 40785006949), t_88f89e55, t_ebcc8f52 and t_4129a258 against t_bdb6c497.

### Code

`hermes_cli/kanban_overlap.py`.

### Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tools/kanban_overlap_failure_cases.json`
- `tests/tools/test_kanban_create_overlap_failure.py`: `test_same_run_duplicate_is_refused_whether_or_not_the_owner_lists_failed_tests`, `test_listed_tests_are_marked_and_still_match_a_card_naming_one`, `test_listed_owner_admits_a_card_naming_another_test_of_its_run`, `test_a_same_run_card_citing_the_listed_owner_is_admitted`, `test_red_main_card_vs_deflake_card_naming_its_own_test_has_no_run_match`, `test_red_main_card_as_filed_is_admitted_against_the_deflake_card`

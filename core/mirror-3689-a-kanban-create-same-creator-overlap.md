# Kanban create: same-creator duplicate guard

Patches: **3655, 3679**.

## Behavior
- A card created by the same creator task as an earlier open card is no longer exempt from the create-overlap check; it is compared like any other card (titles, files, failures) and refused when it looks like a duplicate.
- A re-issue of a card within the same worker run, with a near-identical title and no idempotency key, returns the first card (`created: false, deduplicated: true`) instead of creating a second one. A later run's duplicate is refused with the usual note.
- Legitimate same-creator fan-outs are still admitted: different assignee, review vs non-review title, different leading component tags, enumerated titles (w1-01/w1-02, 1/3 vs 3/3, fixture A/B), and `distinct_from`.
- A re-issue whose title only adds a number (a round or a head sha) is refused, not treated as an enumeration (3679).
- After a completion commits, a detector opens (or comments on) one card when a completion says it duplicates or was superseded by a card from the same creator. Completions that deny duplication ("Not a duplicate of ...") open nothing (3679).
- The worker protocol prompt now says to re-create a card only after the archive succeeded; a refused archive means a worker claimed it, so keep it.

## How it works
- 3655 adds `hermes_cli/kanban_overlap_own.py` with the same-creator admit rules and a same-creator match form (at least 3 shared snake_case identifiers, each named by at most 2 other open cards, title word ratio at least 0.3). `kanban_overlap.py` stops skipping same-creator cards, `kanban_tools.py` and `kanban.py` surface the deduplicated result (the CLI prints the first id, exit 0), and `kanban_db.py` supports the run lookup. `kanban_dup_detector.py` runs after each completion commit and files one collecting card (comment when one is open, new card once it is done or archived).
- 3679 makes the detector skip a duplicate/supersede keyword preceded by a negation within two words in the same clause (earlier-clause negation still counts), and changes `enumerated()` to align the two titles and count markers only in a replaced span with a marker on both sides.

## Code
- `agent/prompt_builder.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_dup_detector.py`
- `hermes_cli/kanban_overlap.py`
- `hermes_cli/kanban_overlap_own.py`
- `tools/kanban_tools.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_dup_detector.py`: `test_three_recorded_duplicate_completions_open_exactly_one_card`, `test_other_creator_and_plain_completions_open_nothing`, `test_a_closed_detector_card_lets_the_next_occurrence_open_a_new_one`, `test_a_completion_that_denies_duplication_opens_nothing`, `test_a_negation_in_an_earlier_clause_does_not_hide_a_duplicate`.
- `tests/tools/test_kanban_create_overlap_same_creator.py` (with fixture `tests/tools/fixtures/kanban_overlap_same_creator_replay.json`): `test_fixture_covers_the_incident_and_ten_fan_outs`, `test_recorded_same_creator_duplicate_is_refused`, `test_same_run_reissues_deduplicate_and_the_later_round_is_refused`, `test_recorded_legitimate_fan_out_is_admitted`, `test_distinct_from_still_admits_an_own_duplicate`, `test_shared_identifiers_many_cards_name_are_not_a_fix_site`, `test_worker_reissue_in_the_same_run_returns_the_first_card`, `test_worker_enumerated_legs_and_distinct_override_still_create`, `test_reissue_that_only_adds_a_round_or_sha_is_refused`, `test_an_added_number_is_not_an_enumeration`, `test_an_index_in_the_same_place_is_an_enumeration`.
- The prompt wording change is untested.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The same-creator match is a heuristic; a duplicate worded apart with no shared identifier can still pass, which is why the completion detector exists.
- The source replay found one false refusal among recorded pairs (two similar fixture-verification cards); `distinct_from` admits it.
- The same-chat (session) exemption remains for cards another creator made.

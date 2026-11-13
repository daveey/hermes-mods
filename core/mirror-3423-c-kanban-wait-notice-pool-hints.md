# Kanban wait notices: pool re-place hints and skip advice

Patches: **3085, 3095, 3102, 3108**.

## Behavior
- Chat-wait notices, ready hints and `kanban show` name a pool worker that could run the card and print the re-place command.
- The hint is printed after the queue clause without repeated dispatcher warnings, survives the admission census branch, and keeps a recorded dispatcher skip alongside it.
- Unrecorded-skip advice defers to the re-place hint; a quota-wall skip offers no pool re-place and keeps its own advice.

## How it works
- 3085 extracts `pool_words()` in `kanban_queue_position` and uses it in `kanban_parent_unclaimed`.
- 3095 keeps the hint under the census branch and moves it to a `pool_hint` payload field.
- 3102 frames a recorded skip first in both branches and shortens unrecorded advice when a pool hint is present.
- 3108 unifies the skip-advice rule and adds `pool_replace_helps()`.

## Code
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/kanban_chat_wait.py`
- `hermes_cli/kanban_dispatch_skip.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_wait_notice_pool_hint.py`: `test_queue_words_carry_the_pool_hint_from_the_shared_helper`, `test_queue_words_without_pool_are_unchanged`, `test_direct_chat_wait_notice_names_the_pool_worker`, `test_parent_unclaimed_notice_names_the_pool_worker`, `test_ready_hint_names_the_pool_worker`, `test_notices_without_pool_name_no_worker`, `test_pool_sentence_comes_from_the_shared_helper_without_repeated_advice`, `test_kanban_show_keeps_the_pool_hint_under_the_admission_census`, `test_kanban_show_keeps_the_recorded_skip_with_a_pool_hint_without_the_census`, `test_options_defer_to_the_pool_hint_when_the_skip_is_unrecorded`, `test_ready_hint_defers_the_unrecorded_advice_to_the_pool_hint`, `test_ready_hint_without_pool_keeps_the_unrecorded_advice`, `test_recorded_skip_with_a_pool_hint_keeps_both_on_every_surface`, `test_pinned_quota_wall_skip_keeps_its_advice_and_drops_the_pool_re_place`, `test_unrecorded_advice_variants_share_one_fact_sentence`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

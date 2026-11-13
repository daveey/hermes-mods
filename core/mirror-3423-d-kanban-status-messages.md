# Kanban show and completion refusal wording

Patches: **3135, 3137, 3142, 3146**.

## Behavior
- `kanban show` sentence-cases skip advice joined after a full stop.
- An expired quota-wall skip reads "(reset passed)", drops the "starts on its own when that wall resets" clause and offers the pool re-place on every surface; a lasting or unreadable wall keeps its wording.
- `kanban_complete` refusing a card with no run names the actual status (`not_running`), with triage advising to promote or archive; `stale_run` is kept for running, blocked, review, done and archived.

## How it works
- 3135 adds `skips.sentence_advice()` and limits pool re-place suppression to a lasting quota wall.
- 3142 adds `_wall_passed(skip, now)`, the exact complement of the suppression, used by `words` and `advice`.
- 3137 classifies refusals by status; 3146 pins the success set (running, ready, blocked, review) in the every-status sweep.

## Code
- `hermes_cli/kanban_dispatch_skip.py`, `hermes_cli/kanban_queue_position.py`, `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/kanban_watch.py`, `tools/kanban_tools.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_hub_admission.py`: `test_kanban_show_sentence_cases_the_skip_advice_after_a_full_stop`, `test_pool_replace_is_suppressed_only_while_the_quota_wall_lasts`.
- `tests/hermes_cli/test_kanban_wait_notice_pool_hint.py`: `test_an_expired_quota_wall_skip_offers_the_pool_re_place_on_every_surface`, `test_kanban_show_marks_an_expired_quota_wall_reset_passed`, `test_a_lasting_or_unreadable_quota_wall_keeps_its_wording`, `test_an_expired_quota_wall_drops_the_reset_clause_on_every_surface`.
- `tests/tools/test_kanban_complete_rejection_class.py`: `test_refusal_names_status_for_every_status`, `test_triage_refusal_says_promote`, `test_unknown_id_is_stale_run`, `test_expected_success_statuses_are_board_statuses`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

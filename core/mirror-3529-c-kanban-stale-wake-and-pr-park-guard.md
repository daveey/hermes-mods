# Kanban stale-wake probe cleanup and PR-park respawn guard

Patches: **3520–3521**.

## Behavior
- A stale scheduled wake for a card that is already done no longer keeps probing: the wake is ignored and its probe row and wake episode are removed, so the next sweep runs no probe for it.
- The respawn guard no longer holds the CI wake of a card that parked on its own PR via the request-review flow. When the parking run's own summary note is the first comment naming that PR, and the park itself names the PR, the park counts as a handoff.
- A note by a different run, or one naming a PR neither earlier comments nor the park name, still keeps the hold.

## How it works
- 3520: the `skip_wake` branch of `_wake` now deletes the `task_wake_checks` row and resets the wake episode in the same write transaction; `restore_missing_checks` skips the held card so it stays gone.
- 3521: `restated_after_park` also treats as known the PRs named by the latest orderly park event: a bare-PR-URL `awaiting.ref`, PR URLs in its reason, and `--repo O/R --number N` in its wake_check. Existing proofs stay (one commented event per row, written by the parking run, at/after the park and before the next claim). A new `park_named_prs` helper parses those fields.

## Code
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_pr_park_note.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_an_ignored_probe_wake_drops_the_probe_so_the_next_sweep_runs_none`, `test_an_ignored_wake_also_drops_the_cards_wake_episode`.
- `tests/hermes_cli/test_kanban_pr_park_names_pr.py`: `test_t1_ship_request_review_park_then_own_note_first_naming_pr_is_not_held`, `test_t1_each_park_field_alone_names_the_pr`, `test_t2_note_also_naming_a_pr_the_park_does_not_name_is_held`, `test_t2_park_naming_nothing_keeps_the_hold`, `test_t3_note_written_by_another_run_is_held`, `test_park_named_prs_reads_only_the_park_fields`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The guard change depends on the park event carrying the PR in one of the three named fields; a park naming nothing keeps the hold.

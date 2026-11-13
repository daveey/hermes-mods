# Kanban parked waits: CI runs that cannot start and `show` park display

Patches: **3257–3258, 3265**.

## Behavior
- Parking a card to await a CI run (`kind=ci`) is refused when the run has a job queued more than 15 min with no runner (`ci_run_unstartable`, naming the labels). A started or freshly queued run parks normally; a bare run id uses the wake check's repo; GitHub errors fail open (logged).
- A card already parked on such a run is woken (with a comment and a shared `runners-offline.<labels>` blocker) when the run stops starting; the sweep reads GitHub at most every 5 min per card.
- The CI run progress code resolves `gh` through the platform resolver (managed-runtime allowlist).
- `kanban show` reads the park through the dispatcher's own lookup, so the displayed backstop matches the wake the dispatcher acts on; a legacy human park with no timed wake says it will be blocked on the next tick instead of "wakes at T". The JSON `wakes` object gains a `blocks_for_human` boolean (display only; scheduling unchanged).

## How it works
- 3257 adds `kanban_ci_progress.ci_run_progress` (reads run jobs via `gh api`) and hooks it into `confirm_remote`, park CLI and the wake-check tick. 3258 resolves `gh` via `hermes_platform`. 3265 adds `kanban_continuation.park_state` / `blocks_for_human` and routes `pending_wake`, `missing_wake`, the wake invariant and `wakes()` through them.

## Code
- `hermes_cli/kanban_ci_progress.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_cli_park.py`
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_wake_summary.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_park_on_a_run_with_no_runner_for_15_min_is_refused`, `test_bare_run_id_uses_the_wake_checks_repo`, `test_started_or_freshly_queued_run_parks`, `test_github_error_fails_open`, `test_parked_card_wakes_when_its_run_stops_starting`, `test_ci_sweep_reads_github_at_most_every_5_min_per_card`.
- `tests/hermes_cli/test_kanban_show_wake.py`: `test_display_backstop_is_the_wake_the_dispatcher_acts_on`, `test_display_and_dispatcher_share_one_park_lookup`, `test_legacy_human_park_says_it_will_be_blocked_not_wakes_at`, `test_legacy_non_human_park_and_timed_human_park_still_say_wakes_at`.
- 3258 (gh resolution) has no dedicated test.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The unstartable check depends on GitHub's job data; on any GitHub error it allows the park.

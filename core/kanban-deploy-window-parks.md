# Kanban parks: deploy window and declared clock waits

Patches: **1783** (`f539daa3ac`, `fix(kanban): deploy parks honour the host core update window [daveey]`); **1804** (`64abd8e9ad`, `fix(kanban): a declared clock wait with a stated future end never re-park escalates; inferred probe refs name the script [daveey]`); **1820** (`db0a1a074e`, `fix(kanban): bound the healthy deploy-window re-park exemption to 24h [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A deploy park reads the host's `cache/core-update/window.json` (schema v1, fresh, this host) and floors its wake to the window, instead of re-parking and escalating while the deploy is only queued (1783).
- A declared clock wait with a stated future end (within 14 days) never re-park escalates; inferred probe refs name the script basename and args (1804).
- The healthy deploy-window exemption is bounded to 24 h from the first park in the streak (1820).

## How it works

`hermes_cli/kanban_deploy_window.py`, `kanban_awaiting.py`, `kanban_timed_park.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1783: A deploy wait parked before the host's core update batch opened re-parked on every wake and its third park escalated to an unstick owner card although the deploy was only queued. Parks now read <root>/cache/core-update/window.json (schema v1, fresh <=1200s, never future, this host as hermes-sync's HOST, well typed). With valid evidence the wake is floored to window_at + drain_bound_s and the reason names the window in Pacific time. The park is healthy (neither counted by prior_same_premise nor escalated) only while the batch is batching/draining and the window is ahead or its semantic state...
- 1804: bb t_01a19f847b7f5abb: bb_gate parks cards on the governor's Claude-pool hold (weekly quota). Core inferred an external wait whose ref was wake_check[:200] ('.../hosts/daveey-min'), and the 3rd park escalated into Opus 'relieve the pool' cards that burned the quota the pool waits on.
- 1820: A kind=deploy park counted as healthy (exempt from same-premise escalation) whenever the host's window.json was fresh, batching/draining and the window ahead. Nothing ties awaiting.ref to the pending batch, so a card waiting on a commit that never reaches main re-parked as healthy forever while other commits kept the window moving; its wake_check was the only exit.

## Code

`hermes_cli/kanban_awaiting.py`, `hermes_cli/kanban_continuation.py`, `hermes_cli/kanban_db_schedule.py`, `hermes_cli/kanban_deploy_window.py`, `hermes_cli/kanban_timed_park.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_awaiting.py`: `test_a_past_or_far_clock_window_still_escalates`, `test_bb_gate_host_holds_still_escalate_on_the_third_park`, `test_bb_gate_pool_parks_with_a_stated_reset_never_escalate`, `test_inferred_probe_ref_names_the_script`
- `tests/hermes_cli/test_kanban_deploy_window.py`: `test_bound_counts_from_the_run_after_another_premise`, `test_foreign_host_window_is_ignored`, `test_future_window_floors_real_parks_and_preserves_probe`, `test_healthy_exemption_expires_after_bound_then_escalates_on_third_park`, `test_healthy_exemption_holds_inside_bound`, `test_healthy_park_is_exempt_after_two_unhealthy_parks`, `test_malformed_or_unusable_window_changes_nothing`, `test_missing_window_still_escalates`, `test_non_deploy_waits_ignore_a_healthy_window`, `test_overdue_batch_whose_state_changes_never_escalates`, `test_stale_window_still_escalates`, `test_unchanged_overdue_batch_escalates_but_keeps_drain_floor`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

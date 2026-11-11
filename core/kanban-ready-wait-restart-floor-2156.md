# Ready-wait alerts: count from the last run's end and name a clock hold

Patch **2134**.

## Behavior

- A card whose run was lost to a gateway restart (booked `owner_restart_lost`) no longer reads as "ready N minutes with no worker" where N includes the run's own running time. The ready-wait floor now includes `owner_restart_lost` and the latest `task_runs.ended_at`.
- This applies to the `held_in_ready` and `stranded_in_ready` diagnostics, the `chat_wait_unclaimed` chat notice, and the ready-since lookup behind `dependency_parent_unclaimed`.
- `held_in_ready` names the guard reason of the current ready episode and, for a run-end clock guard (`owner_restart_cooldown`, `rate_limit_cooldown`, `infrastructure_cooldown`), the hold's end time, shown as "until HH:MM:SSZ" or "expired HH:MM:SSZ" in the title and as `guard_reason` / `hold_until` evidence.
- The chat notice carries `hold_reason` / `hold_until` while a clock guard still holds the card and appends "dispatcher hold <reason> until <time>" to the queue wording. A failed hold lookup drops only the hold, never the notice.
- Alerting only: dispatch and the respawn guard are unchanged.

## How it works

`_READY_WAIT_FLOOR_KINDS` in `kanban_diagnostics.py` gains `owner_restart_lost`. `_last_run_end_ts(runs)` takes the latest `ended_at`; `_last_ready_entry_ts(events, runs)` takes the max of the event floor, resumed-`continued` checkpoints and that run end, so any requeue is bounded even if it left no floor event. `_guard_clock` reads the latest `respawn_guarded` event at or after the episode floor and computes run end plus the configured cooldown (quota-episode backoffs are not modelled; the dispatcher's verdict stays authoritative).

`kanban_chat_wait.py` derives its SQL prefilter from the same constant (`_floor_kinds`) plus a `MAX(ended_at)` subselect. `_clock_hold` uses `guard_hold_until` from `kanban_db_dispatch_clock`; an owner-restart run counts as the cooldown even before the tick that records the guard. `post_notice` in `kanban_parent_unclaimed.py` accepts `extra` payload fields.

## Code

- `hermes_cli/kanban_diagnostics.py`, `hermes_cli/kanban_chat_wait.py`, `hermes_cli/kanban_parent_unclaimed.py`.

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

`tests/hermes_cli/test_kanban_ready_wait_restart_floor.py` (plus a small addition in `tests/tools/test_kanban_unblock_queue.py`): `test_restart_requeue_after_a_long_run_is_not_a_stale_ready_wait` (parametrized with and without the lost event), `test_genuine_unclaimed_wait_after_the_run_still_alerts_with_its_own_age`, `test_active_clock_hold_is_named_with_its_expiry_in_the_notice`, `test_held_diagnostic_excludes_the_prior_run_and_shows_the_active_cooldown_expiry`, `test_held_diagnostic_title_says_until_while_the_clock_hold_is_active`, `test_stranded_diagnostic_floors_on_the_last_run_end_but_still_fires_for_a_genuine_wait`, `test_a_failed_hold_lookup_drops_the_hold_never_the_notice`, `test_a_guard_from_an_earlier_ready_episode_does_not_mask_the_current_hold`, `test_guard_clock_ignores_a_guard_before_the_episode_floor`.

## Applying

Apply ordinal 2134 as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as an independent patch: later patches edit the same files and the replay plan decides which are carried verbatim and which are re-bridged. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- The prefilter SQL is only a filter; `ready_since` decides. Keep both derived from the one constant or notices and diagnostics drift apart.
- Hold times are advisory and ignore quota-episode backoff.
- Other run-ending requeues are bounded by `ended_at`, deliberately not by new event kinds, because pre-claim checks may also write events.

## Could this be a plugin?

No. The diagnostic rules, chat-notice sweep and notice payload live in core Kanban modules with no plugin hook.

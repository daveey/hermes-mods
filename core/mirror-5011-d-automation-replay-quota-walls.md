# Kanban notices: quota-wall replay re-arm, live-refusal rule, held-pool admission

Patches: **4695, 4696, 4739, 4740**

- 4695 (`2aed2167f4`): [daveey] automation replay: a replay refused by a quota wall that still holds waits for the new reset
- 4696 (`128f1053ed`): fix(kanban): require live refusal before repeating unchecked walls [daveey]
- 4739 (`1469a31d4b`): [daveey] kanban notices: a held LLM pool outranks same-label claims
- 4740 (`06bac8cc6b`): [daveey] kanban notices: post_notice uses the held read as its admission

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- A failed automation turn's single delayed replay that is refused by a typed quota wall with a reset still ahead (within 24 h) is re-armed at the new reset, up to three replays in total, on both the turn-failure and build-failure paths. The re-armed replay carries the original deliveries, not a nested note. Other failures still give up.
- Unchecked wall listings now require an attempt or provider free read in this run and a quoted refusal before reasserting; checked-wall instructions and the shared blocker guard are unchanged.
- Stall notices (`post_notice`) and `ready_hint` consult the governor's read-only view when the backlog is busy; if every candidate pool is held, the held pacing admission replaces `matching_pool_busy` and pacing advice is given. A merely paced pool still admits starts, so same-label claims keep queue wording. `post_notice` uses the held result as its admission ahead of the verdict.

## How it works

`tui_gateway/automation_replay.py` (re-arm logic, `WALL_REPLAYS_MAX`), `hermes_cli/kanban_shared_blockers.py` (instruction text), `hermes_cli/kanban_parent_unclaimed.py` (held-over-claims lookup).

## Code

- `tui_gateway/automation_replay.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_parent_unclaimed.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/hermes_cli/test_kanban_notice_pacing_admission.py`: `test_a_held_pool_beats_same_label_claims_on_other_providers`, `test_a_paced_pool_with_same_label_claims_stays_a_queue_wait`
- `tests/hermes_cli/test_kanban_shared_blocker_livelock.py`: `test_unchecked_wall_requires_this_runs_refusal`
- `tests/tui_gateway/test_automation_replay.py`: `test_a_replay_refused_by_a_wall_that_still_holds_waits_for_the_new_reset`, `test_any_other_replay_failure_still_gives_up`, `test_the_rearmed_replay_carries_the_deliveries_not_the_old_note`, `test_a_replay_wake_refused_by_a_wall_that_still_holds_is_rearmed`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- A failing or non-positive governor read keeps existing behavior.
- Replay cap of three bounds repeated waiting.

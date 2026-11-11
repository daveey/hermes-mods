# Kanban no-progress dispatch budget

Patch **2159**.

## Behavior

A card that repeatedly waits without changing its work stops spawning workers at `kanban.no_progress_runs` (default **5**, **0** disables). The dispatcher either re-parks it behind its current usable wake condition with a 24-hour clock fallback, or creates one episode-specific repair owner and makes the card wait on it in `todo`. An owner created by this mechanism that itself loops goes to `triage`, not another owner.

Dispatch results expose `no_progress_held`; events record `no_progress_parked` or `no_progress_escalated`. This is separate from crash and continuation limits and emits neither `continuation_exhausted` nor `goal_unachievable`.

## How it works

`kanban_no_progress.streak` examines closed runs since the latest progress marker. `scheduled`, `blocked` and `continued` into `scheduled` count as waits. A clock-only poll without a condition costs a quarter run; declared measurement windows (`clock_only` or explicit non-inferred `awaiting.kind=clock`) are neutral. Run losses are neutral rather than resetting the streak. Immediate continued work and completion/review handoffs reset it.

Human comments, edits, attachments, links, met conditions and selected parent/child completion, archive, review, reopen and edit events count as progress; agent comments and bare unblocks do not. `hold` rechecks under the write transaction and refuses to displace a live claim or current run. Foreign copies and dry-run dispatch are not mutated. A condition must come from the current episode and have no subsequent broken-probe event. Repair ownership is keyed to the task and its progress marker.

## Code

`hermes_cli/kanban_no_progress.py`, `hermes_cli/kanban_db_dispatch{,_admission}.py`, `hermes_cli/kanban_ops.py`; user-guide addition in `website/docs/user-guide/features/kanban.md`.

## Tests

**Upstream-supplied coverage; not executed in this mirror documentation pass.** `tests/hermes_cli/test_kanban_no_progress.py` exercises real dispatcher admission and stored board state:

- `test_card_wait_loop_parks_behind_its_own_condition`, `test_clock_refallback_reparks_without_spawning`, `test_below_budget_still_spawns`.
- `test_progress_resets_the_budget`, `test_agent_comments_are_not_progress`, `test_counting_rules`, `test_clock_polls_need_the_weighted_budget`, `test_neutral_rows_never_hide_the_streak`.
- `test_blocked_loop_files_one_owner_card_and_no_recovery_loop`, `test_looping_owner_card_goes_to_triage_not_another_owner`, broken/current/obsolete-condition cases.
- `test_live_claim_is_never_taken_off_the_lane`, `test_progress_between_read_and_lock_wins`, disabled/foreign-copy, dry-run, configuration and JSON-payload cases.

The CLI text/JSON formatting helpers are present in the patch but no dedicated CLI invocation test is added there; end-to-end CLI output is untested here.

## Applying

Follow the full ordered replay in [README.md](README.md), with its replay plan and bridges. Do not apply this patch independently.

## Gotchas

A bare unblock is not evidence of progress; requeueing an unchanged card does not solve the loop. The fallback can re-park without a new worker. This budget measures recorded board progress, not arbitrary external changes a worker has not yet reported.

## Plugin?

No. The hold must compose with core dispatch admission, write-lock revalidation and task claims.

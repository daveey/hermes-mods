# Kanban sync: pre-rename boards, request budgets and a visible stopped sync loop

Patches: **1830** (`67afe41997`, `fix(kanban-sync): a board older than the failure-counter rename is not schema-ahead [daveey]`), **1834** (`7c0cbd2923`, `fix(kanban): recompute retry and redirect request budgets [daveey]`), **1847** (`4dfb4cc1f0`, `[daveey] kanban sync: name a stopped sync loop in the stuck warning, sync status and create`), **1869** (`6b0afbba9a`, `[daveey] kanban sync: regression tests for the sync-down paths; explicit runs_on refusal carries rejection=sync_not_running`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A board created before the failure-counter column rename (which keeps the old `spawn_failures`/`last_spawn_error` columns) is no longer misread as schema-ahead, so sync starts on it (1830).
- Retry and redirect request budgets in the sync client are recomputed per request instead of shared across attempts (1834).
- When the sync loop is not running, the dispatcher-stuck warning, `hermes kanban sync status` and `kanban_create` name the stopped loop and its reason (1847); an explicit `runs_on` refusal carries `rejection=sync_not_running`, with regression tests for the sync-down paths (1869).

## How it works

`hermes_cli/kanban_db_sync.py` treats the ADD-first renamed columns as known; `hermes_cli/kanban_sync_runstate.py` records the loop's down state, read by the stuck warning, sync status and the create tool.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1830: e4b3ebe805's guard refuses to sync a board that holds columns this core neither syncs nor keeps local, reading them as a newer core's migration. The failure-counter rename (#20410) is ADD-first (kanban_db_connect. _RENAMED_TASK_COLUMNS): it adds consecutive_failures / last_failure_error, fills them from spawn_failures / last_spawn_error and keeps those. Every board older than the rename still holds them, so the MacBook gateway, on the current core, logged at boot (2026-11-02 20:47 PT)
- 1834: fix(kanban): recompute retry and redirect request budgets [daveey]
- 1847: Incident (MacBook, 2026-11-02 20:47 PT): the gateway logged "kanban sync: not starting: board default: this core does not declare columns a newer core added (tasks: last_spawn_error, spawn_failures)" once, and its sync watcher never respun. With sync enabled every claim goes through the Board lease, so the board stalled (52 outbox ops unpushed, 15 ready, 0 running). Nothing said why: the dispatcher-stuck warning named only claim_deferred / skipped_ineligible counts, `hermes kanban sync status` printed last_error: None (it reads outbox rows only), and kanban_create(placement="hub") kept retu...
- 1869: #779 follow-up. Mutants of 4dfb4cc1f02a that survived the schema-guard tests now each fail a test: the load_identity start refusal, the mid-loop SchemaAhead give-up, the crash record, heavy work while sync is down (machine check + stranded check together), the courier exemption, and record_down keeping since.

## Code

`hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_db_dispatch_stuck.py`, `hermes_cli/kanban_sync_runstate.py`, `tools/kanban_tools.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_sync_hub_keepalive.py`: `test_get_resend_recomputes_remaining_budget`, `test_redirect_chain_recomputes_budget`
- `tests/hermes_cli/test_kanban_sync_schema_guard.py`: `test_board_migrated_from_before_the_rename_is_not_schema_ahead`, `test_board_migrated_from_before_the_rename_pushes`, `test_run_forever_starts_on_a_board_migrated_from_before_the_rename`, `test_retired_columns_do_not_hide_a_newer_cores_column`, `test_every_rename_source_column_is_declared_retired`, `test_refused_sync_start_is_named_by_the_stuck_dispatcher_warning`, `test_refused_sync_start_is_named_by_sync_status`, `test_healthy_round_clears_the_sync_state`, `test_sync_loop_exit_while_enabled_is_recorded`, `test_sync_state_is_ignored_when_sync_is_off`, `test_machine_pin_create_with_sync_running_makes_a_runs_on_row`, `test_machine_pin_create_while_sync_is_down_never_returns_a_stranded_row` (+9 more)
- `tests/hermes_cli/test_kanban_worker_placement_failures.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

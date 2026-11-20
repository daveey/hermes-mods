# Primary-board current-run replica evidence

Mirror entries 5014 and 5015.

## Behavior

On a primary-synced board, when local execution authority is refused only because `mode==primary`, `session_tasks` / counts / queue-position attach replica **current-run** evidence (executor event UID/time via `kanban_replica_execution.run_evidence`) so a running card projects `state=running` with a portable `run_id` and `freshness=current`, `source=kanban.replica_run`.

- Never fabricate a run or clock.
- Non-running or malformed rows stay explicit unknown.
- OFF/SHADOW unchanged. Remote replica rows unchanged.
- List and counts must agree (C9).
- Routing fields stay out of local list rows (ownership inside private `_replica_run.scope`). 5015 keeps that ownership out of card routing.

REST/terminal/execute_code entry paths for this projection: untested here (session list/count/queue-position consumers only).

## How it works

When the local adapter refuses authority solely for primary mode, the session-activity and queue-position readers call `run_evidence` instead of inventing a local run. Incomplete, legacy, or clock-expired evidence stays unknown. 5015 records ownership on the private replica-run scope so list-row routing shape is unchanged.

## Code

- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_replica_execution.py`
- `hermes_cli/kanban_execution_observations.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_primary_execution.py`

## Tests

Source tests (not executed here): `tests/hermes_cli/test_kanban_primary_execution.py`

- `test_primary_current_run_projects_running_in_every_consumer` (running + heartbeat → running + `run_id`)
- `test_incomplete_primary_evidence_remains_unknown` / `test_primary_missing_legacy_schema_stays_unknown` (no heartbeat / malformed → explicit unknown)
- `test_primary_adapter_failure_does_not_fallback_to_local_authority` (OFF/SHADOW/remote-style refusal is not rewritten)
- `test_primary_ask_survives_in_list_and_counts` (list/count agree)
- `test_primary_preserves_local_row_routing_shape`
- `test_primary_clock_expires_without_rejuvenation`, `test_primary_uses_latest_beat_of_current_run_only`

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply these mails on their own.

## Gotchas

- A missing heartbeat is unknown, not a synthesized run.
- Do not copy `_replica_run.scope` onto public routing fields.

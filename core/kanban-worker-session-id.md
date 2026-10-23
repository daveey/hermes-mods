# Kanban task API exposes each run's worker session

Patches:

- `patches/0211-feat-kanban-task-API-exposes-each-run-s-worker_sessi.patch`: `worker_session_id` on every run in
  `GET /api/plugins/kanban/tasks/{id}`.

## Behavior

- Each entry of `runs` in the task detail endpoint carries `worker_session_id`: the session the worker actually runs
  in. Clients (the Desktop task drawer's "Open worker session") open that transcript instead of `tasks.session_id`,
  which is the chat that CREATED the card.
- A running run already has it (registered when the worker session is created, and again on compression rotation);
  a finished run falls back to `metadata.worker_session_id`; neither = `null`.

## How it works

`hermes_cli/kanban_worker_recovery.worker_session_ids(conn, task_id)` reads `worker_recovery_sessions` (patch 0117)
as `{run_id: session_id}`, newest registration winning, and returns `{}` on any SQLite error.
`plugins/kanban/dashboard/plugin_api._runs_with_worker_sessions` merges it into the `asdict(run)` dicts.

## Code

- `hermes_cli/kanban_worker_recovery.py`: `worker_session_ids`.
- `plugins/kanban/dashboard/plugin_api.py`: `_runs_with_worker_sessions`, used by `get_task`.

## Tests

`tests/hermes_cli/test_kanban_worker_recovery.py`: `test_worker_session_ids_maps_running_run_to_its_worker_session`,
`test_dashboard_runs_expose_worker_session_id`. Passed on `origin/main` d79e1139d1 (Oct 22, 2026 PT, with the rest of
that file). The Desktop click path is not covered here.

## Applying

`git am -3` in order (after 0117).

## Plugin?

No: the task detail endpoint is core's kanban dashboard plugin.

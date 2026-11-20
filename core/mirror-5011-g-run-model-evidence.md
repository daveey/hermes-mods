# Run evidence: admission-bound session models

Mirror entries 4991–4994.

## Behavior

A class-routed author run could end without a `worker_result`, leaving only selector evidence on the Board, so ship could not establish reviewer independence.

- **4991** every terminal run event is stamped with the concrete models read from the admitting host's managed `state.db`. Admission, local origin, managed path and in-run session start are all required; unknown evidence is omitted rather than guessed. A new idempotent `attest-run-models` CLI backfills history (including evicted foreign copies) by appending testimony without waking or altering closed tasks.
- **4992** `evidence()` runs inside the board write transaction, so state.db is opened with `wait_for_tracking=False`; a busy registry omits evidence (which grants nothing). Admission-window boundary guards are pinned.
- **4993** includes recursive descendant sessions' models and usage; any descendant uncertainty omits all evidence.
- **4994** history attestation waits until the run has ended and then captures late children.

## How it works

`kanban_run_models.evidence(conn, task_id, run_id)` is called from event stamping in `kanban_db_events.py`; `kanban_run_models_cli.py` provides `attest`; parser/dispatch in `kanban_parser.py` and `kanban.py`.

## Code

`hermes_cli/kanban_run_models.py`, `kanban_run_models_cli.py`, `kanban_db_events.py`, `kanban.py`, `kanban_parser.py`.

## Tests

Source tests (not executed here): `test_kanban_run_models` (terminal evidence survives board replay, unknown/foreign omitted, real lifecycle transitions, historical attestation does not reset wake episode, local read-only idempotent attest, evicted copy hydration, CLI parser, dead-pid finalizer, recursive descendants, waits for run end, never waits on tracking reservation).

## Applying

Use the full segmented recipe in the core mods [README](README.md) ("Applying"); never independent patches.

## Gotchas

- Evidence is omitted on any doubt; absence is not proof of non-independence.
- Never block the writer transaction on SQLite tracking.

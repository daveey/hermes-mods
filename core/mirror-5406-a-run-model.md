# Newest-run model labels on session cards

Mirror entries: 5061, 5062.

## Behavior

- Listed cards may show a display-only `run_model` for their newest run.
- Concrete session models on terminal/backfill attestation events beat the newest route label. A live route may remain a class selector such as `class:deep`.
- A newest run with no usable model stamps nothing, never a model from an older run; failed/legacy reads also stamp nothing.

## How it works

`add_run_models()` batches requested card IDs, binds each to `MAX(task_runs.id)`, and scans that run's route, terminal and `run_models_attested` events in order. Concrete `session_models` are deduplicated for display; the newest route, including an unknown route, is tracked separately. Card-detail enrichment calls this helper without changing model selection.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_run_model.py`
- `hermes_cli/kanban_session_activity_card_details.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

`test_kanban_run_model.py` covers classes and concrete pins, real terminal-event kinds, backfilled attestation, reroute order, concrete-over-route precedence, newest unknown/just-claimed runs, independent cards, malformed payloads and old boards without event tables. Actual Desktop running-row rendering and remote publication of this field are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_run_model.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

A route class is not a concrete attestation. Labels report event evidence and do not pin a model or change routing. Never fall back to older-run evidence simply because the latest run is unknown.

## Why core instead of a plugin

Only core has the authoritative task-run/event join used by every card-detail consumer; plugins should consume the derived label rather than independently guess the current run.

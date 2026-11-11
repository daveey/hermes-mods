# Kanban placement treats an explicit running initial status as default

Patches:

- `patches/2190-fix-kanban-treat-explicit-running-initial-status-as-.patch`

An explicit `initial_status="running"` (the tool default) counted as an uncarried option, so otherwise portable tasks lost worker-first placement.

## Behavior

- `portable_reason` ignores `initial_status == "running"`; `None` was already ignored; `blocked` still reports `uncarried option` (it needs origin-side human-request handling).
- Other uncarried options (for example `max_retries`) still make a task non-portable alongside `running`.
- The args dict is not mutated.

## How it works

One predicate change in `portable_reason`.

## Code

`hermes_cli/kanban_placement.py`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/hermes_cli/test_kanban_worker_placement.py` (None/running/blocked, `decide` policy, no hidden-option bypass). Only the `portable_reason`/`decide` path is covered.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

The CLI path was already equivalent and is not touched.

## Plugin

None.

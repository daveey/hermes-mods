# CLI create judges placement on the resolved assignee

Patches: **2456**.

## Behavior

`hermes kanban create` without `--assignee` persisted the resolved assignee on the card (the calling profile when run from a worker or agent shell, otherwise the board default), but placement was judged as if the assignee were `default`. A card whose resolved assignee was not on the allowlist was therefore audited as portable and, with `worker_pools` configured, enrolled into `worker-first-v1`. Now placement policy and the creation audit are computed from the same assignee the card is created with. An explicit `--assignee` behaves as before.

## How it works

`_cmd_create` in `hermes_cli/kanban.py` passes the resolved `assignee` (the value given to `kb.create_task`) to `cli_policy(..., assignee=assignee)` and `cli_audit(..., assignee=assignee)`. In `kanban_worker_placement`, `_cli_fields` now overrides the `assignee` taken from `vars(args)` (which is `None` when the flag is absent) with that keyword-only argument, so `creation_decision` and `creation_audit` see the persisted value. See [kanban-worker-first-placement.md](kanban-worker-first-placement.md) for the enrollment boundary.

## Code

`hermes_cli/kanban.py`, `hermes_cli/kanban_worker_placement.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_cli_resolved_assignee.py`. Covered entry path: the CLI slash form of `create` (`cli.run_slash("create ... --json")`) across with/without worker pools, explicit/implicit assignee and agent/non-agent shells, checking the persisted assignee, the `created` event's `portable_reason` and `placement_policy`. The `kanban_create` tool and REST create paths are untested here (they do not use `_cli_fields`).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- `cli_policy` and `cli_audit` now require the keyword-only `assignee`; any out-of-tree caller must pass it.
- Cards already created with the wrong audit are not rewritten.

## Plugin

Core change; no plugin required.

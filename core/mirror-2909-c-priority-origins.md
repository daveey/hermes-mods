# Priority migration validates origin coverage before staging

Patches: **2901**.

## Behavior

Priority migration checks its explicit origin-to-host map against one canonical capture before consuming backup paths or attachments. A new operator-only read-only `origins` operation reports coverage; extra mapped origins with no captured rows are skipped and never contacted.

## How it works

**2901:** separate read-only `_capture` from `_prepare_stage`, compare origin counts/missing/extra and require a mapped local writer owning captured rows. On refusal return errors with no staging. Successful migrate stages that exact checked manifest, verifies its origins still match and only contacts captured origins. `origins` accepts the map/writer flags but rejects attachment/task/backup/manifest/digest and rollback-policy flags, retaining the operator/worker fence.

## Code

- `docs/kanban-priority-rollout.md`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_priority_cli.py`
- `hermes_cli/kanban_priority_runner.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_kanban_priority_runner.py` covers missing/superset maps, rowless/unmapped writer, same-object capture staging, staged-origin mismatch, CLI origins reporting/flag refusals/operator fencing, and a real capture path proving no backup, attachment or priority mutation on coverage refusal.

- `tests/hermes_cli/test_kanban_priority_runner.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

Hosts are never inferred from origin IDs. Extra entries alone are not an error; missing entries are. Coverage refusal consumes nothing, but a later staging failure may already have consumed fresh backup paths. This document does not authorize an actual priority migration.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

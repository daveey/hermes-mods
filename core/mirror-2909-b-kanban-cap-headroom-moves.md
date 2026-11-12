# Kanban line-cap headroom: hub route and flag-parser moves

Patches: 2832.

- **2832** (`03881ea37f`, kanban cap headroom: Hub route wrappers into kanban_sync_hub_routes, create flag parsers into kanban_cli_flags)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- Hub route wrappers (leases, repin, recover, reserve, machines, report_pickup) move into `HubRoutesMixin` in `kanban_sync_hub_routes.py`; sessions and report_metrics keep their own handling in `kanban_sync_client`. Workspace/branch flag parsers move to `kanban_cli_flags` and are imported under the old names. No behavior change.

## How it works

- `hermes_cli/kanban.py`, `kanban_cli_flags.py` (new), `kanban_sync_client.py`, `kanban_sync_hub_routes.py` (new).

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_cli_flags.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_hub_routes.py`

## Tests

- No test files; covered by existing suites that exercise the moved code.

Test files touched by these patches:

- (none)

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Pure moves conflict easily with parallel edits to the same files, notably the recovery patches (2817-2830) and 2831.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.

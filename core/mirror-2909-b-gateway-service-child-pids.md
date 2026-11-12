# Gateway: a unit's `gateway run` child behind a wrapper is a service gateway

Patches: 2793, 2810.

- **2793** (`30343a2ff9`, gateway: a unit's `gateway run` child behind a wrapper ExecStart is a service gateway)
- **2810** (`96997240c0`, gateway: service-pid walk stops at the unit's gateway, not its whole subtree)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2793: when a systemd unit's ExecStart is a wrapper (e.g. a token broker exec chain), MainPID is the wrapper and the gateway is its child. `_get_service_pids` returned only MainPIDs, so `hermes update`'s manual sweep treated the unit's gateway as a manual one, drained it and the relaunch fought the unit for the gateway lock (the unit looped on exit 75). It now adds the `gateway run` descendants of each unit MainPID.
- 2810 (review fix): the walk stops at the first gateway and returns nothing when MainPID is itself the gateway, so a `gateway run` started by an agent in the gateway's subtree stays a manual gateway. A wrapper whose own cmdline matches the gateway matcher is detected by a matching direct child and walked through. Comments are corrected: these PIDs are targeted by `kill_gateway_processes`/`gateway stop --all`, not only protected.

## How it works

- `hermes_cli/gateway_service_members.py` (`gateway_descendant_pids`), `hermes_cli/gateway.py`; `_parse_ps_line` and `_iter_windows_list_processes` moved unchanged to `hermes_cli/gateway_process_listing.py` to stay under the line cap.

## Code

- `hermes_cli/gateway.py`
- `hermes_cli/gateway_process_listing.py`
- `hermes_cli/gateway_service_members.py`

## Tests

- `tests/hermes_cli/test_gateway_service_child_pids.py` with real process trees: wrapper MainPID child included, update sweep leaves the unit gateway alone, walk stops at the wrapper-child gateway (nested worker gateway excluded), no descent when MainPID is the gateway, matching wrapper walked through.

Test files touched by these patches:

- `tests/hermes_cli/test_gateway_service_child_pids.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Linux/systemd behavior; real-process-tree tests may be platform-sensitive.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.

# Kanban lanes: worker ceilings, nullable valves and dependency blockers

Mirror entries 5192.

## Behavior

- `max_workers` persists with the lane; ready and review candidates are filtered and the observed running count is rechecked at the local claim CAS. Zero stops new claims (no priority or alarm exception); null leaves behavior unchanged. It uses the existing fleet census and is explicitly not an atomic cross-gateway reservation.
- Omitted daily limits are now distinguished from explicit null: `lanes set` preserves an omitted value, clears an explicit null, and exposes repeatable `--unset` and `--max-workers`.
- Dependency waits in flow report all open parent ids and their PRs, keep the first-blocker shorthand and avoid inferring the card's own PR.

## How it works

- `hermes_cli/kanban_lane_workers.py` (ceiling), `kanban_admission.py`, `kanban_db_dispatch_queries.py` (candidate filter), `kanban_lanes.py`, `kanban_lanes_cli.py`, `kanban_lanes_flow.py`, `kanban_parser.py`, docs `features/kanban.md`.

## Code

- `hermes_cli/kanban_lane_workers.py`
- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_db_dispatch_queries.py`
- `hermes_cli/kanban_lanes_cli.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_lane_valves.py` (`test_worker_limit_validation`, `test_set_preserves_omitted_and_clears_explicit_null`, `test_cap_filters_and_atomically_rechecks_claim`, `test_zero_unlimited_shadow_and_default_lane`, `test_cli_unset_and_omission`, `test_zero_cap_has_no_priority_or_alarm_exception`), `test_kanban_lanes_flow.py` (`test_flow_limits_and_all_open_blockers`). Cross-gateway races are untested by design.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Not atomic across gateways: two gateways can briefly exceed the ceiling.

## Why core instead of a plugin

The ceiling is enforced in the core claim transaction and candidate query.

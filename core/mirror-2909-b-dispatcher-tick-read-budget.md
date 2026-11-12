# Gateway dispatcher tick read budget

Patches: 2792.

- **2792** (`c4bf691bf0`, perf(gateway): dispatcher tick reads profile names only and each other board once)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- `_receipt_scope` called `list_profiles()` twice per board per tick (config and liveness probes per profile) just to take names; it now uses `list_profile_names()`.
- `count_running_tasks_other_boards` opened every other board from every board's tick. Inside `kanban_board_share.tick_counts()` (one tick) a board's count is kept until that board's own tick asks, so later boards still see workers it started. Outside a tick every call reads every board as before.
- Review fixes: `gateway_names` does not remember a failed hub.db read; `repair_divergence` skips a row that left the replica mid-pass instead of raising KeyError.

## How it works

- `gateway/kanban_watchers_dispatcher.py` (one count scope per tick), `hermes_cli/kanban_board_share.py` (`tick_counts`), `hermes_cli/kanban_origin.py`, `hermes_cli/kanban_sync_divergence.py`.

## Code

- `gateway/kanban_watchers_dispatcher.py`
- `hermes_cli/kanban_board_share.py`
- `hermes_cli/kanban_origin.py`
- `hermes_cli/kanban_sync_divergence.py`

## Tests

- `tests/hermes_cli/test_dispatcher_tick_read_budget.py` (profile-name listing, ~once-per-board reads, earlier-board workers counted in-tick, per-call reads outside a tick, gateway tick runs inside one scope, failed hub read not kept, divergence repair skips vanished row); touched `tests/gateway/test_kanban_dispatcher_health.py` and `test_kanban_external_drain_hold.py`.

Test files touched by these patches:

- `tests/gateway/test_kanban_dispatcher_health.py`
- `tests/gateway/test_kanban_external_drain_hold.py`
- `tests/hermes_cli/test_dispatcher_tick_read_budget.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- The caching is scoped to a tick; do not use `tick_counts` outside the dispatcher.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.

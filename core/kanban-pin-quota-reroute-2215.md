# Far quota wall reroutes a non-mandatory model pin

Patches:

- `patches/2197-fix-kanban-a-far-quota-wall-moves-a-non-mandatory-mo.patch`

A card pinned to one model/provider whose quota wall was days away stayed held for the whole wall, since workers cannot re-pin and no supervisor edit existed.

## Behavior

A pin is moved to a same-class peer for one run only when all hold: one of the card's own runs hit a wall on the pin's provider and it is still up; it resets `kanban.pin_quota_reroute_min_hours` (default 6) or more from now; the pin is not mandatory (`tasks.model_pin_mandatory` or the `require_model` marker); the lane is ready, not review; the pinned model is in exactly one routing class of the assignee's policy and an eligible, unwalled route of that class exists (no fallback to default or strong tiers).
- The pin row is never written; the reroute applies to the in-memory claimed task, so the first run after the reset uses the pin again.
- Each rerouted admission records a `model_reroute` event (from/to, provider, resets_at, episode, run) and tier reason `pinned+pin_quota_reroute:<provider>`, plus one card comment per wall naming the opt-out. If the record cannot be written the run keeps its pin (`pinned+pin_quota_reroute_unrecorded`).
- Mark a pin mandatory with `kanban_create(pin_mandatory=true)`, `hermes kanban create --pin-mandatory` or `hermes kanban set-model <id> [--mandatory|--no-mandatory]`; clearing the pin clears the mark; workers stay fenced.

## How it works

`kanban_pin_reroute.pinned_route` is called from the claim/tier path and from the quota-episode preview (preview equals admission and writes nothing). The new nullable column is added by the later-task-column migration and carried by Board sync.

## Code

`hermes_cli/kanban_pin_reroute.py` (new), `kanban_quota_episode.py`, `kanban_model_tier.py`, `kanban_db.py`, `kanban_db_connect.py`, `kanban_db_sync.py`, `kanban_assignment_routing.py`, `kanban.py`, `kanban_parser.py`, `config_defaults.py`, `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`, `website/docs/user-guide/configuration.md`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/hermes_cli/test_kanban_pin_reroute.py` (thresholds, mandatory flag, review lane, class membership, walled peers, comment dedupe, return to pin, preview equals admission, failed record keeps pin, create/set-model/tool/sync wiring) and the updated `test_kanban_quota_episode.py`. The CLI flags and the `kanban_create` tool argument are covered only through those wiring tests.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

A pin in zero or two routing classes is never moved. The threshold config falls back to the default for invalid values.

## Plugin

None.

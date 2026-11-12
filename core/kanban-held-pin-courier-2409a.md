# Held-machine pins and lossless courier placement

Mirror entries: [2274](patches/2274-fix-kanban-refuse-a-pin-to-a-held-machine-never-drop-runs-on-on-a-courier-create-daveey.patch), [2275](patches/2275-fix-kanban-refuse-courier-loss-of-mandatory-model-pins-daveey.patch).

## Behavior

- Refuse creation pins naming a remote pickup machine on Board hold, with created=false, machine_held and unheld alternatives.
- Explain an existing card's all-held machine lane as ON HOLD rather than merely queued.
- Never silently drop runs_on (including an explicit empty list) or mandatory model pins through the creation courier.

## How it works

placement-derived machine labels now pass through check_machine_pins while sync is available. MachinePinRefused carries held state and alternative unheld pickup machines. The queue description distinguishes all-held candidates from labels still served by an unheld machine. runs_on and pin_mandatory enter UNCARRIED_OPTIONS: default routing keeps the card local, and an explicit incompatible courier destination is refused before any send.

## Code

- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_sync_runstate.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_held_machine_pin.py` — `test_tool_create_pinned_to_a_held_machine_is_refused_and_writes_nothing`, `test_tool_create_pinned_to_an_unheld_machine_still_lands_with_its_label`, `test_cli_create_pinned_to_a_held_machine_is_refused`, `test_a_card_already_waiting_on_a_held_machine_reads_on_hold_not_queued` (additional cases in this file).
- `tests/hermes_cli/test_kanban_placement.py`
- `tests/hermes_cli/test_kanban_placement_runs_on.py` — `test_runs_on_card_pinned_to_the_hub_is_refused_not_sent_without_its_labels`, `test_runs_on_card_with_a_new_client_is_still_refused`, `test_runs_on_is_never_among_the_options_sent_to_the_target`, `test_runs_on_card_is_not_pool_portable` (additional cases in this file).

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

A hold is an intentional operator drain/migration boundary: choose another machine or await release, do not clear the hold to place work. While sync is down the existing gateway route remains unchanged. The tests cover tool placement/runs_on, CLI runs_on and queue descriptions; this is not a live registry or lease audit.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.

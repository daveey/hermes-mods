# Reject impossible machine-label conjunctions

Patches 2070–2071.

## Behavior

On a primary Board, reject a nonempty `runs_on` conjunction that no enrolled machine can satisfy, before writing a task. Keep unknown registry admission behavior when registry data is missing, empty, lacks the current machine, or retrieval raises.

## How it works

Creation normalizes labels and checks the complete machine mapping, including held/inactive machines, before the existing profile-tool check. A refusal distinguishes capability labels from gateway placement and `machine:<registered-name>` pins.

## Code

`hermes_cli/kanban_runs_on_capabilities.py`.

## Tests

`tests/hermes_cli/test_kanban_runs_on_label_admission.py`: database and create-tool refusal with unchanged row counts, accepted held machines, empty labels, registered pins, missing/empty/incomplete registries and retrieval exceptions.

Execution status is recorded in [mirror 2072 verification](mirror-2072-verification.md); listed regression cases alone are not an execution claim.

## Applying

Use the complete snapshot’s [README Applying recipe](README.md#applying), including its replay plan and policy. These patches depend on earlier core changes.

## Gotchas

Unknown registry data is not proof of impossibility. Admission does not promise a worker is currently available. CLI and REST end-to-end creation are untested here.

## Could this be a plugin?

No standalone plugin is supplied: this changes core dispatch/admission or turn-finalization wiring.

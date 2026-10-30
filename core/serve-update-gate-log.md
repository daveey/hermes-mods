# Serve update-gate log

Patch: 1292.

## Behavior

Desktop SSH serves write stderr to a per-spawn `desktop-ssh/<id>/<nonce>.log` that Desktop deletes when the backend is replaced, so a gate wait or re-exec left no trace. Each update-gate message is now also appended, with timestamp and pid, to `<HERMES_HOME>/logs/serve-update-gate.log` (rotated at 2 MB, write errors ignored). The re-executed image records `re-executed image #N gated on <sha>` under the same pid, so a live re-exec can be proven afterwards.

## Code

`hermes_cli/serve_update_wait.py`.

## Tests

`tests/hermes_cli/test_serve_update_wait_reexec.py::test_gate_record_survives_an_unwritable_home_and_rotates`. This mirror task did not execute it.

## Applying

Apply 1292. No hotkeys or config.

## Gotchas

Only gate waits and re-execs are logged, not general serve output. No plugin involved.

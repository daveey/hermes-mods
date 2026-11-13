# Gateway exec spawn ownership and discovery test stability

Patches: **2943, 2949, 2950**.

## Behavior
- A quick-command exec cancelled during spawn kills its child; a cancelled spawn failure preserves the cancellation.
- The command-discovery fixture's graceful-shutdown wait uses a 120 s bound instead of 20 s, so CPU starvation in CI does not flake it.

## How it works
2949 keeps spawn ownership across cancellation in `run_inbound_hooks`; 2950 adds a success-path control test; 2943 only raises test timeouts.


## Code
- `gateway/run_inbound_hooks.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_exec_quick_command_tree.py`: `test_cancelled_exec_during_spawn_kills_child`, `test_cancelled_exec_spawn_failure_preserves_cancellation`, `test_exec_command_success`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
2943 changes no product code.

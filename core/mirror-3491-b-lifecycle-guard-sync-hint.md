# Lifecycle guard: public command walk and sync hint

Patches: **3483**.

## Behavior
- The refusal hint that points at the sync tool now survives `bash -o pipefail /path/hermes-sync`: the values of `-o`/`-O`/`+o`/`+O` are no longer mistaken for the script operand, and parsing stops at `-c`.
- Leading shell reserved words (`if`, `then`, `elif`, `else`, `while`, `until`, `do`, `!`, `time`, `{`, plus `time -p` / `--`) are peeled before env assignments and wrappers, so `time hermes-sync`, `! hermes-sync` and `if ...; then hermes-sync; fi` still get the hint.
- Known gap, recorded in the patch: the guard itself still allows keyword-prefixed script runs (`time ./x.sh`, `{ ./x.sh; }`, `while ...; do ./x.sh; done`) inside the supervised gateway when the script contains a gateway restart. Only the hint is fixed here; the keyword cases are tested through the hint function, not through a full refusal.

## How it works
3483 moves the command walk out of the terminal guards module into `cron/lifecycle_guard.py` as the public `executes_command_named(command, name)`, next to `contains_launchctl_submit_command`. `_runs_hermes_sync` in the terminal guards now just calls it, removing the import of six private helpers. The shell-script operand scan skips option values and the keyword peel runs before argv0 detection.

## Code
- `cron/lifecycle_guard.py`
- `tools/terminal_tool_guards.py`
- `tests/tools/test_lifecycle_guard_hermes_sync_hint.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_lifecycle_guard_hermes_sync_hint.py`: `test_hint_survives_shell_options_with_values`, `test_hint_survives_reserved_words`, `test_a_shell_option_value_is_not_the_script`, `test_executes_command_named`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The guard still fails open for keyword-wrapped script invocations (see Behavior); this patch does not close it.

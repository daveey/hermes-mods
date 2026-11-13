# Lifecycle guard: clustered shell options and reserved-word walk

Patches: **3622**.

## Behavior
- `bash -euo pipefail /x/hermes-sync`, `bash -eo pipefail ...` and `bash --rcfile FILE ...` no longer mistake the option value for the script, so the refusal hint for the sync tool is kept.
- Referenced-script scanning handles clustered `-o` values (`bash -euo pipefail ~/deploy.sh` scans the script), and `--rcfile`/`--init-file` values are still scanned as sourceable files (fail closed). Stdin-fed forms (`bash -euo pipefail < f.sh`, `cat f.sh | bash -eo pipefail`) scan `f.sh`.
- External `time(1)` after an assignment or wrapper (`x=1 time ...`, `env time -f %e ...`) is peeled; `time -- -p cmd` is not treated as running `cmd`; `coproc NAME` is only peeled before a compound command.

## How it works
- 3622 adds `_shell_options_end` / `_shell_script_operand` handling of `o`/`O` in short clusters, `--rcfile`, `--init-file`, other long options and a lone `-`; unifies keyword peeling into `_peel_reserved_words` over `_RESERVED_PREFIX_WORDS`; adds `_peel_external_time` in `executes_command_named`. `_TRANSPARENT_COMMAND_PREFIXES` is unchanged.

## Code
- `cron/lifecycle_guard.py`
- `tests/cron/test_lifecycle_guard_interpreter_runners.py`
- `tests/tools/test_lifecycle_guard_hermes_sync_hint.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_lifecycle_guard_hermes_sync_hint.py`: `test_clustered_option_value_and_rcfile_stay_refused` (parametrized); other cases were added to existing tests whose names the patch does not show as new defs.
- `tests/cron/test_lifecycle_guard_interpreter_runners.py`: stdin-fed cases added to existing tests (names not shown in the patch as new defs).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Shell parsing here is heuristic; unusual option layouts may still be misread.

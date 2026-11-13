# Lifecycle guard: shell-parsing fail-open fixes

Patches: **3663, 3673, 3685**.

## Behavior
- The lifecycle guard now opens a lifecycle `.py`/`.sh` script that is fed to an interpreter through a brace group, an if/while/until/for/select/case body, a glued `<` redirection (`python3 -<f.py`), or process substitution (`python3 <(cat f.py)`, `bash < <(cat f.sh)`, `source <(cat f.sh)`). Previously these ran the script unscanned.
- `{ cat f | grep x; } | python3` stays allowed (the cat is piped elsewhere inside the group); `cat f.py | python3 <(echo hi)` is no longer a false positive.
- `zsh -eO restart.sh` no longer swallows the script as an option value: in zsh only `o` consumes a token. Other shells keep `O` value-taking.
- `bash clean.sh --rcfile /dev/null` is no longer refused: `--rcfile`/`--init-file` are only examined before the script operand.
- `bash - -x /x/hermes-sync` treats `-x` as the script (hermes-sync is just an argument); `coproc sudo -u root hermes-sync` is recognized as running the command (3685 pins this; production behavior unchanged).

## How it works
- 3663 moves the group walk into `cron/lifecycle_guard_groups.py` (the guard module is at its line cap); the walker takes helper callables so it never imports the guard. A closed group writes on what its commands wrote; compound keywords open groups closed by fi/done/esac in command position; a process substitution suspends its command and on `)` is read once per file it writes (a NUL placeholder if none). `split_glued_redirections` splits an unquoted, unescaped `<`/`>` glued to a non-fd word before shlex; quoted ones stay one word.
- 3673 makes `_shell_options_end` take the shell name and limits the `--rcfile` loop in `_references_at` to the option region before `operand_index`.
- 3685 adds test rows only, killing two surviving mutants (`"-"` handling in `_shell_options_end`, the coproc NAME bound in `_peel_reserved_words`).

## Code
- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_groups.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_compound_feeds.py`: `test_compound_stdin_feeds_are_scanned`, `test_compound_mentions_and_data_stay_allowed`, `test_split_glued_redirections_is_quote_aware`.
- `tests/cron/test_lifecycle_guard_interpreter_runners.py`: `test_shell_specific_option_values_find_the_script`, `test_rcfile_after_the_script_operand_is_not_sourced`.
- `tests/tools/test_lifecycle_guard_hermes_sync_hint.py`: new parametrize rows (zsh `-eO`, `bash - -x`, `coproc sudo -u root`) in existing tests such as `test_executes_command_named` and `test_hint_survives_reserved_words`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The zsh `-O` rule is deliberately wider than "bash only": `sh` may be bash (where `-O` takes a value), and dash/ksh reject unknown `-O`, so reading a value there only skips a command that never runs.
- The redundant `_peel_external_time` was left in place.

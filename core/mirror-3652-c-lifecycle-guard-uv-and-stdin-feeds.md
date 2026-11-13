# Lifecycle guard: uv options and hidden stdin feeds

Patches: **3645**.

## Behavior
- The lifecycle guard now refuses commands that run a file restarting the gateway even when the file is hidden behind `uv run` value options (`-w`, `--upgrade-group`, `--*-package`, `--python-preference`).
- Stdin feeds are scanned: leading redirection (`< f.py python3`), glued redirection (`python3<f.py`), `cat < f.py | python3`, and piped groups (`cat f.py | (python3)`, `| { true; bash; }`).
- `uv run -` (program on stdin) scans stdin and pipe feeds.
- Mentions and plain data stay allowed.

## How it works
- 3645 adds `cron/lifecycle_guard_uv.py`, a uv argv reader following `uv help run` value options and flags, with getopt-style short clusters. An unknown option is read both ways (flag, then value-taking) and every reading is scanned, so unknown options fail closed.
- `_command_segment_views` adds a view with command-position redirections moved to the end; only the command word is split at a glued `<`/`>`, never arguments.
- `_iter_segment_contexts` keeps a stack of the stdin feed each `(` / `{` group inherits.

## Code
- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_uv.py`
- `tests/cron/test_lifecycle_guard_stdin_feeds.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_stdin_feeds.py`: `test_uv_run_value_options_do_not_hide_the_script`, `test_hidden_stdin_feeds_are_scanned`, `test_mentions_and_data_stay_allowed`, `test_uv_value_options_cover_uv_help_run`, `test_uv_option_arity`, `test_uv_run_readings`, `test_hoisted_redirection_keeps_arguments`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Option tables track one uv version's `uv help run`; newer uv options rely on the fail-closed double reading.

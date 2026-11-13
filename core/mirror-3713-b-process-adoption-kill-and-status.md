# Process adoption: parse-failure status, foreign-kill settlement, unknown docks

Patches: **3703**.

## Behavior
- An adopted background job whose command has a syntax error (or an unterminated heredoc that swallows the closing paren) now records exit status 2 instead of settling as `unknown`. An unterminated heredoc behaves as it does unwrapped (warning, runs).
- `process_manage kill` from a process that does not supervise the job now settles the job as killed (-15, termination source `process.kill`, "terminated by process.kill") rather than "status unknown", whether the job was adopted or its spawner still holds it. A kill that leaves the root alive withdraws the marker so a later natural exit is not misreported.
- Foreign poll/wait results get the local post-processing (wait/poll policy and the masked-success hint).
- The CLI process dock and the TUI process roster show an unrecorded exit (`unknown`, `watcher_lost`) with a `?` glyph and "status unknown" verdict, never as done.

## How it works
- 3703 makes the exit recorder `eval` the shell-quoted command in the child, so a parse error ends only the child and the recorder writes 2.
- A foreign kill writes a `<log>.killed` marker before signalling; the output-log reader settles a marked exit as killed, foreign poll/log/wait exit fields read it, and the marker retires with the log after result retention.
- `_handle_process` applies `apply_wait_poll_policy` and `with_masked_success_hint` after the foreign fallback.
- New locale key `cli.dock.verdict_unknown` is added to all 17 catalogs; `cli_process_dock`, `processRoster` and `processGlyph` map the unknown reasons.

## Code
- `tools/process_registry.py`
- `tools/process_registry_foreign.py`
- `tools/process_registry_output_log.py`
- `hermes_cli/cli_process_dock.py`
- `ui-tui/src/app/processRoster.ts`
- `ui-tui/src/lib/processGlyph.ts`
- `locales/*.yaml` (17 catalogs)
- `tests/tools/test_process_restart_adoption_matrix.py`
- `tests/hermes_cli/test_process_dock.py`
- `ui-tui/src/__tests__/processDock.test.tsx`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_process_restart_adoption_matrix.py`: `test_parse_failure_cases_match_the_unwrapped_command`, `test_foreign_kill_that_leaves_the_job_alive_withdraws_its_kill_marker`, `test_foreign_wait_gets_the_masked_success_hint_like_a_local_one`, `test_foreign_kill_of_a_job_its_spawner_still_supervises_settles_as_killed`, `test_a_kill_marker_retires_its_log_like_a_recorded_exit`.
- `tests/hermes_cli/test_process_dock.py`: `test_unrecorded_exit_status_is_never_painted_as_done`.
- `ui-tui/src/__tests__/processDock.test.tsx`: a test that a re-adopted job with no recorded exit status is not painted as done.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Aliases defined earlier in a command now apply to later lines, as in an unwrapped `bash -c`.
- Translated catalogs receive the new key; wording per language is not covered by a test.

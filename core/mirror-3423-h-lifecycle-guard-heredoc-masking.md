# Lifecycle guard: heredoc body masking and nested-shell proofs

Patches: **3365–3374**.

## Behavior
- The terminal lifecycle guard no longer refuses read-only commands just because a file path appears inside a quoted heredoc fed to a data consumer (for example a Python or sqlite client reading from stdin); such bodies are treated as data, not executed scripts.
- Masking works behind stdin-passing wrappers (`timeout`, `nice`, `env VAR=...`, `command` and similar), inside shell function bodies, and for data-consumer heredocs nested in a quoted outer body (for example an ssh remote `bash -s`). Call-syntax paths are mentions.
- Masking fails closed whenever the shape could let the body run as shell: unproven variable bindings, hijacked stdin, wrappers with unlisted options, `-i`/`-l`/`--login` shells (startup files and prompt hooks can rewrite stdin), `zsh`, keyword-led subshells, and any alias that may rename a consumer, wrapper or shell.
- Aliases captured by the terminal backend's session snapshot are visible to the guard; unknown aliases (no snapshot yet) count as possible.
- Options after `-s` on a stdin shell are validated up to the operands, so `bash -s -i`, `-o history -o histexpand` and the ssh remote form stay refused.

## How it works
- 3365 teaches `tools/shell_heredoc.py::strip_inert_heredoc_bodies` to classify a quoted heredoc by its peeled consumer, accept positional args after `-`, function-body openers and nested bodies (depth-bounded), and treats call-syntax paths as mentions in `cron/lifecycle_guard.py`.
- 3366 closes four fail-open shapes: a per-wrapper option allowlist, nested bodies judged only for the executed-path walk, a literal single non-`<<-` owner handing its body unchanged to a stdin shell, and function-body consumers (never `cat`).
- 3367 requires proven variable bindings (unique standalone top-level assignment) and intact stdin before nested masking.
- 3368 preserves the shell prefix and startup provenance; reserved environment assignments are not transparent.
- 3369 refuses verbatim-stdin status to startup-reading shells and sets a walk-wide alias flag for `function NAME ( ... )` style keyword-led subshells.
- 3370 and 3371 make the startup-file execution proof in the tests hermetic: restore PATH under a PATH-resetting system profile, and gate the proof on the startup file actually being sourced.
- 3372 validates every option after `-s` (with an allowlist for `-o`/`+o` operands).
- 3373 adds a seeded generated shape corpus (shell options x wrappers x nesting forms x payloads) run against real bash, with checked-in verdicts of the earlier behavior.
- 3374 makes the snapshot bootstrap print captured alias names, exposes them through `lifecycle_guard_session_aliases()` to `scan_gateway_lifecycle(session_aliases=...)`, and runs the startup-file proofs from `scripts/run_tests.sh`.

## Code
- `cron/lifecycle_guard.py`
- `tools/shell_heredoc.py`
- `scripts/run_tests.sh`
- `tests/cron/heredoc_shape_corpus.py` (corpus generator)
- `tests/cron/fixtures/lifecycle_guard_heredoc/` (script fixtures and checked-in verdicts)

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_heredoc_walk.py`: `test_data_consumer_heredoc_mention_is_not_refused`, `test_shell_consumers_and_outer_bodies_stay_visible`, `test_deeply_nested_heredocs_stay_bounded`, `test_masked_consumer_mentions_are_still_read`, `test_probe_table_plain_rows_stay_allowed`, `test_probe_table_controls_still_refused`, `test_path_in_interpreter_call_is_a_mention`, `test_call_argument_mentions_are_still_read`, `test_subshell_and_substitution_parens_still_execute`, `test_refused_scripts_verbatim_are_not_refused`, `test_bodies_a_shell_runs_stay_refused`, `test_untrusted_owner_masks_nothing`, `test_trusted_owner_masks_the_data_body`, `test_untrusted_variable_assignment_does_not_hide_executed_body`, `test_synthesized_mutator_cannot_change_a_trusted_shell_binding`, `test_ssh_binding_with_unknown_expansions_can_change_remote_program`, `test_variable_binding_requires_unique_standalone_top_level_assignment`, `test_variable_binding_accepts_unique_standalone_assignment`, `test_nonreading_units_use_a_generic_positive_grammar`, `test_cli_and_file_substitutions_are_not_proven_nonreading`, `test_nested_stdin_hijack_walk_only_body_stays_visible`, `test_nested_masking_requires_literal_owner_and_quoted_outer_body`, `test_nested_masking_stops_after_untrusted_same_line_or_heredoc_owner`, `test_nested_function_second_body_is_not_assumed_nonreading`, `test_same_line_binding_mutation_keeps_transformed_body_visible`, `test_shell_startup_environment_cannot_authorize_nested_masking`, `test_reserved_environment_assignments_are_not_transparent`, `test_outer_prefix_must_preserve_nested_interpreter_identity`, `test_keyword_operands_before_parens_still_execute_scripts`, `test_startup_files_and_prompt_hooks_cannot_authorize_nested_masking`, `test_startup_reading_shells_are_not_verbatim_stdin_shells`, `test_plain_stdin_shells_still_nest_mask`, `test_keyword_led_subshells_still_execute_scripts`, `test_options_after_dash_s_are_still_validated`, `test_history_expansion_after_dash_s_runs_walk_only_body`, `test_generated_shape_corpus_never_allows_what_bash_runs_and_main_refuses`, `test_live_ssh_body_reading_kanban_db`.
- `tests/cron/test_lifecycle_guard_session_aliases.py`: `test_reviewer_repro_snapshot_time_alias_fails_closed`, `test_unknown_session_aliases_fail_closed`, `test_session_alias_shadowing_a_consumer_keeps_heredoc_bodies_visible`, `test_bootstrap_alias_names_round_trip_through_real_bash`, `test_guard_reads_backend_hook_on_the_class_only`, `test_local_backend_reports_snapshot_aliases_and_terminal_guard_refuses`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The alias check is deliberately broad and fails closed; legitimate commands that define aliases may keep the scan-cap refusal.
- Startup-file proof tests depend on the runner's shell startup behavior; 3370, 3371 and 3372 add fallbacks that assert only guard verdicts (with a warning) when the host does not source the test's startup file.
- The corpus test compares against checked-in verdicts of an earlier baseline, so it only guards against regressions relative to that snapshot.

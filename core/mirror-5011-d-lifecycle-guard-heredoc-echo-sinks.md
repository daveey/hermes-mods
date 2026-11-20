# Lifecycle guard: heredoc filters, quoted echo parentheses, output process sinks

Patches: **4701, 4702, 4712, 4713, 4714, 4715, 4716, 4722**

- 4701 (`b91bf6f434`): [daveey] Keep numeric head/tail heredoc pipelines as interpreter data
- 4702 (`151ccaab3f`): [daveey] Quote interpreter paths in login filter execution proofs
- 4712 (`379b71bbb6`): [daveey] Keep quoted echo parentheses out of substitution command positions
- 4713 (`bdcc7bbdd2`): [daveey] Exclude echo function headers from quoted-argument proof
- 4714 (`cf631abc6d`): [daveey] Preserve echo binding uncertainty across output sink checks
- 4715 (`887ba8af2b`): [daveey] Use portable bash shebang in echo binding marker fixture
- 4716 (`33f93c6b7e`): [daveey] Keep sink masking mutant compatible with binding context
- 4722 (`43227271a7`): [daveey] Treat output process substitutions as shell sinks

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Numeric head/tail pipelines after a single heredoc stay interpreter data; shell sinks and rebinding stay visible.
- Parentheses in a quoted literal echo argument are data, unless echo may be shadowed (functions, aliases, source, dynamic words), in which case the sink is retained.
- Stdout redirected into a process substitution running a shell is a sink.

## How it works

`tools/shell_heredoc.py`, `tools/shell_arith_sink.py`, `cron/lifecycle_guard_output.py` and cron tests with real Bash markers.

## Code

- `tools/shell_heredoc.py`
- `tools/shell_arith_sink.py`
- `cron/lifecycle_guard_output.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/cron/test_lifecycle_guard_echo_binding.py`: `test_rebound_echo_real_shell_and_public_scanner`, `test_uncertain_binding_retains_sink`, `test_original_binding_context_survives_secondary_mask`, `test_heredoc_mask_keeps_original_binding_uncertainty`
- `tests/cron/test_lifecycle_guard_login_filter.py`: `test_login_interpreter_filter_is_data`, `test_shell_suffix_stays_visible`, `test_explicit_rebinding_stays_visible`, `test_login_shell_action_still_refused`, `test_multihop_diagnostics_shape`, `test_filter_certificate_not_used_by_text_scanners`, `test_uncertain_filter_certificate`, `test_unknown_suffix_not_certified`, `test_head_rebinding_not_certified`, `test_generated_data_shapes`, `test_real_bash_filter_and_alias_controls`
- `tests/cron/test_lifecycle_guard_output_process_sink.py`: `test_output_process_shell_refuses_oversized_script`, `test_generated_real_bash_marker`, `test_output_process_data_stays_data`, `test_process_shell_sink_vocabulary`, `test_process_sink_boundary_controls`
- `tests/cron/test_lifecycle_guard_output_sink.py`: modified only
- `tests/cron/test_lifecycle_guard_quoted_echo_sink.py`: `test_quoted_parentheses_are_data`, `test_generated_real_bash_output_execution`, `test_quote_like_data_does_not_mask_real_anchor`, `test_arithmetic_output_remains_a_sink`, `test_uncertain_or_nested_context_keeps_original_sink`, `test_quoted_parentheses_in_command_word_execute`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Binding uncertainty must survive secondary masking; 4715/4716 are fixture-only follow-ups.

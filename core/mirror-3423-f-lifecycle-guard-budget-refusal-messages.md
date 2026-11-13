# Lifecycle guard budget refusals name the file and the cap

Patches: **3238, 3290–3291**.

## Behavior
- When the lifecycle guard refuses a script because its scan budget ran out, the message names the file (or the root command / cron prompt and script) and which limit was hit: bytes, lines, or longest line, with the cap or what remained of it. Example shape: "`<path>` has N lines, over the 16384-line scan cap (text at depth 1)".
- The reported line count is the file's own count (matches `wc -l`), not the budget's charged count; when the own count would fit, the charged count is named too ("has 3 lines (4 counted), over the 3-line scan cap").
- A file with exactly the cap's lines read after earlier text spent budget reports what was left rather than "over the whole cap".
- Verdicts are unchanged; only messages changed. A mentioned (not executed) over-budget script is still not a refusal; non-text refusal wording is unchanged.

## How it works
- 3238: `charge_text` records which limit refused; the walk threads the quoted source path into `_budget_exhausted` for referenced scripts and their `sh -c` payloads; cron names its script and prompt; the terminal guard and cron caller messages state the real budget.
- 3290 reports the text's own line count (minus the join newline cron adds for an empty prompt).
- 3291 names the whole cap only when the own count exceeds it or nothing was spent, else the remainder.

## Code
- `cron/lifecycle_guard.py`
- `tools/terminal_tool_guards.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_budget_refusal.py`: `test_line_cap_refusal_names_script_and_cap`, `test_line_cap_refusal_names_remaining_budget`, `test_long_line_refusal_names_script_and_cap`, `test_byte_cap_refusal_names_script_and_cap`, `test_root_command_refusal_names_the_command_and_cap`, `test_root_char_count_refusal_is_a_lower_bound`, `test_cron_refusal_names_prompt_and_script`, `test_scan_budget_summary_formats_sizes_exactly`, `test_non_text_budget_refusal_wording_is_unchanged`, `test_sh_c_payload_inside_script_keeps_script_name`, `test_mentioned_script_over_budget_is_not_a_refusal`, `test_caller_message_states_the_real_scan_budget`, `test_line_count_without_trailing_newline_counts_the_last_line`, `test_line_count_at_the_cap_names_the_charged_count`, `test_cron_script_without_prompt_verdict_still_charges_the_join`, `test_cap_line_file_after_earlier_text_is_over_what_is_left`.
- `tests/cron/test_lifecycle_guard_heredoc_walk.py` was adjusted by 3238 (no new test names).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The "charged" count (newlines + 1) still decides the verdict; only the displayed count changed, so a file can be refused while showing a count at or under the cap (the message then names the charged count).

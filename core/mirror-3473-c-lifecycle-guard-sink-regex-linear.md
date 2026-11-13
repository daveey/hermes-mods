# Lifecycle guard: linear-time output-sink regex

Patches: **3460**.

## Behavior
- The cron lifecycle guard's shell output-sink regex no longer backtracks quadratically on heredoc bodies that were masked down to runs of newlines. Large no-sink heredocs scan quickly (the source commit reports 16k lines going from 15.4 s to about 1.5 s in the guard, and 0.0 s on 50k newlines for the regex alone; these are source-commit measurements, not mirror-run).
- Matching semantics are unchanged: newline was already an anchor, so `[ \t]*` matches the same commands as `\s*` did.
- Fail-closed behavior is pinned: a sink producer that only reads an oversized (>1 MiB) data file is refused on the scan cap.

## How it works
3460 replaces `\s*` with `[ \t]*` in the command-word-substitution alternative of `_SHELL_SINK_RE`, and documents in `_iter_printed_shell_payloads` that literals charge the shared line/byte budget. `tools/shell_heredoc.py` gets a small accompanying change.

## Code
- `cron/lifecycle_guard.py`
- `tools/shell_heredoc.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_output_sink.py`: `test_sink_regex_is_linear_on_masked_newline_runs`, `test_large_no_sink_heredoc_scan_stays_fast`, `test_producer_reading_an_oversized_data_file_under_a_sink_fails_closed`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

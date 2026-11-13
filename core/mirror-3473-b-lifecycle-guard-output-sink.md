# Lifecycle guard: consumer heredoc output run by a shell is not data

Patches: **3459**.

## Behavior
- When a heredoc consumer's output is run as shell code (for example `python3 - <<'PY' | bash`, `eval "$(...)"`, `bash <(...)`, `. <(...)`), the lifecycle guard no longer treats a printed script path as a mere mention. An oversized script printed that way now fails closed on the scan cap, as would a printed restart.
- Without such a sink nothing changes: data mentions (`connect('/d/big.db')`, `| grep`, `|| true`, `o=$(...); echo`) stay allowed.

## How it works
3459 adds `output_reaches_shell()` in `tools/shell_heredoc.py` recognising shell-running sinks (pipes into bash/sh/sudo/ssh, `eval`, process-substitution sources, here-strings, `sh -c "$(...)"`, `$(...)` as command word). `strip_inert_heredoc_bodies` masks nothing when one is present, and `cron/lifecycle_guard.py` recurses every quoted string literal as an executed command under such a sink. A new corpus family (`s_position == "sink"`) in the shape corpus runs real bash over sink x wrapper x nesting x consumer x line combinations; the regenerated baseline verdicts fixture is included.

## Code
- `tools/shell_heredoc.py`
- `cron/lifecycle_guard.py`
- `tests/cron/heredoc_shape_corpus.py`
- `tests/cron/fixtures/lifecycle_guard_heredoc/main_verdicts.json`
- `tests/cron/test_lifecycle_guard_heredoc_walk.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_output_sink.py`: `test_consumer_output_fed_to_a_shell_fails_closed`, `test_review_function_repro_is_not_masked_for_the_walk`, `test_multiline_consumers_feeding_a_shell_mask_nothing`, `test_consumer_output_not_fed_to_a_shell_stays_a_mention`, `test_output_reaches_shell`, `test_output_does_not_reach_shell`.
- `tests/cron/test_lifecycle_guard_heredoc_walk.py`: `test_shape_corpus_catches_a_guard_that_keeps_masking_sink_bodies`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Detection is shape-based; sinks outside the recognised shapes are untested.
- The corpus runs real bash (with a fake sudo) and is heavy.

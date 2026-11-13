# Lifecycle guard: echo option words no longer hide the printed command

Patches: **3585**.

## Behavior
- Under an output sink, `echo -n bash /x/huge.sh | bash`, `echo -ne`, `echo -n -e`, `echo -E` under `eval "$(...)"`, and `printf -- bash\ /x/huge.sh` are now walked as executing the printed command and are refused where the plain form is, instead of being allowed.
- The change can only refuse more, never less.

## How it works
3585 makes `_printed_argument_lines` in `cron/lifecycle_guard.py` yield, besides the line as written, the line without echo's leading `-[neE]+` option words and (fail closed) the line without every leading `-word`, for echo and printf. Duplicates are removed so the walk budget is unchanged for plain lines. The test corpus gains new sink shapes and a consumer, and the recorded main-verdicts fixture is regenerated.

## Code
- `cron/lifecycle_guard.py`
- `tests/cron/heredoc_shape_corpus.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`
- `tests/cron/fixtures/lifecycle_guard_heredoc/main_verdicts.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_output_sink.py`: data-driven; new sink shapes echo-n, echo-ne, echo-n-e, echo-E-eval, echo-n-wrapped, printf-dashdash, and a new `echo-n` sink consumer. No new named `test_` functions.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The regenerated verdicts fixture grows by about 300 lines; the old allows are unchanged and only new allows were added for the new shapes.

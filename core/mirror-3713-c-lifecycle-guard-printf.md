# Lifecycle guard: printf and function-keyword shapes

Patches: **3706**.

## Behavior
- Under an output sink, the cron lifecycle guard now treats the text printed by `printf` (with its format applied) as shell code, so `printf '%s %s' bash /x/huge.sh | bash` is no longer missed and the oversized script gets scanned.
- `function NAME { echo ...; }; f | bash`, `echo -n` and `time -p echo` variants are pinned as caught.
- Fails closed: every new variant only adds more text that is walked as executed.

## How it works
3706 makes `_printed_argument_lines` also yield printf arguments without the format (covers unreadable formats like `"$f"`) and the result of `_printf_output(format, arguments)`: each conversion takes the next argument as text, `%%` becomes `%`, and the format repeats while arguments remain, capped at 64 rounds. The function-keyword peel was already fixed on main by the shared reserved-word peel; this patch only pins it in tests. The printf model lives in the new `cron/lifecycle_guard_printf.py` to keep `lifecycle_guard.py` under its line cap.

## Code
- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_printf.py`
- `tests/cron/fixtures/lifecycle_guard_heredoc/main_verdicts.json`
- `tests/cron/heredoc_shape_corpus.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_output_sink.py`: `test_printf_output_applies_the_format`, `test_printf_output_stops_after_the_round_cap`, `test_printer_with_no_printed_words_does_not_abort_the_walk`, plus new shape-table entries for echo/printf variants.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Not modelled: `*` width, `.N` precision and `%b` escapes in printf formats.

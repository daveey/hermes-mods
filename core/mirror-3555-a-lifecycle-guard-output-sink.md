# Lifecycle guard: output-sink quote pairing and echo/printf producers

Patches: **3531**.

## Behavior
- The guard that refuses oversized shell payloads now sees `print('bash /x/huge.sh')` under `| bash` even after a stray apostrophe of the other quote kind earlier in the text.
- `printf 'bash /x/huge.sh\n' | bash` and `echo bash /x/huge.sh | bash` are recognized as producers feeding a shell.
- `| grep bash` and `| grep -c bash` count as sinks (fails closed by design); `| grep bashrc` and `| grep bash.sh` do not.

## How it works
3531 rewrites the quoted-literal regexes in `_iter_printed_shell_payloads` as lookaheads so the span after every quote is yielded. Each literal is also yielded with `\n\t\r\v\f` escapes turned into newlines, and under a sink the argument line of executed `echo`/`printf` commands is yielded (the extra tokenization only runs when the text mentions echo or printf). The heredoc-shape corpus gains python-apostrophe, printf and echo consumers (+1764 sink cases) and the verdict baseline file is regenerated.

## Code
- `cron/lifecycle_guard.py`
- `tools/shell_heredoc.py`
- `tests/cron/heredoc_shape_corpus.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`
- `tests/cron/main_verdicts.json` (regenerated data, large diff)

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_output_sink.py`: `test_consumer_output_not_fed_to_a_shell_stays_a_mention`, `test_output_reaches_shell` (both modified by this patch; the corpus file supplies the new shapes).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Treating any later pipe word `bash` as a shell is intentionally over-broad (fails closed).

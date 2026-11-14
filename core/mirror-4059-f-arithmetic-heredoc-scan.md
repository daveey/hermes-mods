# Arithmetic sink scanning across quotes and heredocs

Mirror batch 6; ordinals 4041, 4042, 4043, 4044.

## Behavior

Quote-like data, reused heredoc delimiters and executable literal bodies cannot hide later arithmetic substitution sinks. Numeric date-producer exemptions remain narrow.

## How it works

The scanner shares delimiter parsing, distinguishes ANSI-C escaped quotes, tracks unclosed quote/backtick state and recursively scans unmasked executable/unknown literal heredoc bodies. Expanding bodies with substitutions fail conservatively. Inert masking preserves exact terminators while blanking body text, preventing a later same-named delimiter from swallowing intervening commands. Depth is capped at 32; extracted fast paths avoid unnecessary newline token scanning when no heredoc exists.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/cron/test_arith_quote_desync.py`
- `tools/shell_arith_sink.py`
- `tools/shell_heredoc.py`

Mail entries in the ordered series:

- 4041: [`fe24e27943a547f5ca6d1f12bf66958ac3bfb686`](patches/4041-fix-shell-keep-arithmetic-sink-scanning-aligned-across-quoted-data-daveey.patch)
- 4042: [`a2269b5dab401e4816722fed30a402b94aebe6a9`](patches/4042-fix-guards-retain-masked-heredoc-terminators-daveey.patch)
- 4043: [`f7885abe32c3792e57429c5e171a2fd8602fc66b`](patches/4043-fix-shell-scan-executable-literal-heredoc-bodies-for-arithmetic-sinks-daveey.patch)
- 4044: [`a0e7a51c06679aaf38d9303925e796d202912ad4`](patches/4044-refactor-shell-isolate-arithmetic-scan-fast-paths-daveey.patch)

## Tests

`tests/cron/test_arith_quote_desync.py`: apostrophes in quoted/unquoted/multiple/tab-stripped heredocs and ANSI-C data preserve later sink detection and date exemptions. Unclosed quotes refuse; reused delimiters keep intervening oversized execution visible; masked output retains its own terminator. Bash/env/SSH/unknown literal consumers remain scannable. Real Bash marker executions never occur on a guard-allowed row; inert cat and platform-sh controls distinguish data from execution. Nested date and depth-limit cases pin recursive behavior. Patch 4044 is an extraction-only follow-up using this same suite.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

Uncertain syntax may conservatively refuse benign commands. Only callers may prove and mask inert input; this is not permission to treat every quoted heredoc as inert.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

# Bash printf output-sink reconstruction

Mirror batch 6; ordinals 4024, 4025, 4026, 4027.

## Behavior

Lifecycle scanning follows commands produced by printf, echo escapes and literal printed strings into shell sinks, including command/path spellings assembled through widths, precision, escapes and numeric conversions.

## How it works

The model consumes star-width/precision operands, truncates strings, decodes format versus percent-b escapes, handles percent-b stop, format reuse and printf variable-assignment options. C-style numeric rendering uses exact Fraction-based X87/double/quad models plus hand-written integer corner cases. Output/field/round caps bound construction, while raw/arguments-only and legacy whole-argument variants remain. Huge decimal inputs saturate before Python’s digit limit; exponents are bounded and zero mantissas exit early. Model errors skip that variant without aborting surrounding conservative walks. Unknown format escapes consume only the backslash, preserving a following percent conversion and argument alignment.

## Code

Source paths touched by this feature group (including regression fixtures):

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_printf.py`
- `cron/lifecycle_guard_printf_number.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`

Mail entries in the ordered series:

- 4024: [`5ad9d5fccfec7f1df25b654e0f1b2dcdcc67f644`](patches/4024-daveey-lifecycle-guard-printf-width-precision-Ns-truncation-b-escapes-and-v-NAME.patch)
- 4025: [`8e269edf3f8ea44b51b2a4fe90e1445b04ebea2a`](patches/4025-daveey-lifecycle-guard-printf-C-exact-numbers-long-double-models-output-cap-whol.patch)
- 4026: [`9cb9d3652dcdec4caaec523914568545fdff81e6`](patches/4026-daveey-lifecycle-guard-printf-saturate-4300-digit-numbers-instead-of-aborting-th.patch)
- 4027: [`14ba6e4a26886f61fc2a2f2509153acdc7d5ed69`](patches/4027-daveey-printf-guard-preserve-conversions-after-unknown-format-escapes.patch)

## Tests

`tests/cron/test_lifecycle_guard_output_sink.py`: sink shapes refuse oversized executed scripts for star operands, truncation, percent-b hex/octal/stop, variable assignment, echo/print escapes, numeric filename corners, huge decimals/precision and unknown escapes. No-sink controls remain allowed. Byte comparisons use real Bash and accept output from the host-compatible float model. Assertions also establish output-budget equality, bounded width/rounds, multi-model and whole-argument retention, numeric rounding carry, escape semantics and continued scanning after injected model failure.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

This is conservative command scanning, not a complete Bash interpreter. The documented model assumes UTF-8 Unicode decoding; C locale may retain non-ASCII escapes. Empty percent-c omits the NUL Bash drops from script input. Control-character percent-q precision is not claimed fixed.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

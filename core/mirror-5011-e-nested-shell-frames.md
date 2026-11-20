# Nested shell frames, arithmetic and bounded scanning

Mirror entries: 4753, 4754, 4755, 4756, 4757, 4758, 4759, 4760.

## Behavior

Nested arithmetic, quoted substitutions and executable newlines remain visible to the lifecycle guard without treating arithmetic shifts as heredocs. Executable nesting that exhausts the scanner is refused instead of escaping through a fallback.

## How it works

Balanced subscript matching skips quoted and escaped brackets. A shared arithmetic context distinguishes declarations, compound assignments and shell command boundaries. Undecodable dollar-quoted heredoc words are not guessed. Quote-boundary tags preserve the adjacency of dollar quoting, including ANSI-C NUL handling. Append-only output fragments cache their length rather than repeatedly summing prior fragments. Break offsets propagate through double-quoted command and parameter frames, while an additional whole-word view preserves outer arguments and redirections. Public scan boundaries catch both the substitution limit and Python recursion exhaustion.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_lines.py`
- `cron/lifecycle_guard_words.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Nested-subscript and arithmetic-frame matrices distinguish data from later commands. Heredoc fallback and token-dedup regressions cover dollar quoting. Frame-accounting tests assert deterministic linear work and normalized break offsets. Nested-review regressions pair executable examples with harmless literal twins and exercise public boolean boundaries.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/cron/test_lifecycle_guard_frame_accounting.py`
- `tests/cron/test_lifecycle_guard_frame_arithmetic.py`
- `tests/cron/test_lifecycle_guard_heredoc_frame_fallback.py`
- `tests/cron/test_lifecycle_guard_nested_review_regressions.py`
- `tests/cron/test_lifecycle_guard_nested_subscripts.py`
- `tests/cron/test_lifecycle_guard_token_dedupe.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

This is conservative static analysis, not a Bash interpreter. Literal deep nesting is not equivalent to executable over-budget nesting. Later heredoc work has its own bounded alternate-view handling; this document does not imply every delimiter can be decoded exactly.

# Lifecycle guard: nested command frames, comments, case patterns and heredocs

Patches: **4741, 4742, 4743, 4744, 4745, 4746, 4747, 4748, 4749, 4750, 4751, 4752**

- 4741 (`4a9967741a`): [daveey] Preserve nested shell contexts in lifecycle comment scanning
- 4742 (`4081ac886f`): [daveey] Normalize backtick comments nested in parameter words
- 4743 (`3818f9c2f7`): [daveey] Honor nested expansions inside double-quoted parameter words
- 4744 (`7b9aa3609d`): [daveey] Frame nested command substitutions before comment normalization
- 4745 (`3ddd28f3f0`): [daveey] Test ANSI-C words alongside descriptor ampersand protection
- 4746 (`76b972a015`): Split logical lines at newlines inside nested $() frames
- 4747 (`b29d7248a7`): Close nested-comment backslash and case-pattern fail-opens in framed ${...}
- 4748 (`1482716800`): Track case pattern position so a `case` pattern opens nothing
- 4749 (`cd3d21ef91`): Extract case-clause tracking from _command_span into _CaseClauses
- 4750 (`8d596d51d9`): Skip here-document bodies in $(...) frames so they open no case
- 4751 (`ed8a0e47f5`): fix(cron): fall back when speculative heredocs obscure command frames [daveey]
- 4752 (`3406dfac36`): fix(cron): retain nested grammar through redirection encoding [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Nested shell contexts, backtick comments, double-quoted parameter fallbacks and ANSI-C words keep correct boundaries.
- Newlines inside nested frames split logical lines; nested-comment backslashes and case-pattern `)` no longer close frames early.
- Case tracking is phase-aware (`_CaseClauses`), here-document bodies are skipped as data, and speculative heredocs fall back to conservative frames.

## How it works

`cron/lifecycle_guard_lines.py`, `cron/lifecycle_guard_tokens.py`, `cron/lifecycle_guard_words.py`, nested-comment and heredoc-fallback tests.

## Code

- `cron/lifecycle_guard_lines.py`
- `cron/lifecycle_guard_tokens.py`
- `cron/lifecycle_guard_words.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/cron/test_lifecycle_guard_heredoc_frame_fallback.py`: `test_arithmetic_shift_is_not_a_heredoc`, `test_unrecognized_heredoc_does_not_open_phantom_case`
- `tests/cron/test_lifecycle_guard_nested_comments.py`: `test_nested_context_does_not_hide_following_command`, `test_ansi_nul_truncates_only_quoted_span`, `test_ansi_ampersands_preserve_words_and_descriptor_redirects`, `test_newline_in_nested_substitution_separates_commands`, `test_case_word_as_pattern_does_not_hide_following_command`, `test_heredoc_body_does_not_open_case`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- 4749 is a no-behavior refactor.

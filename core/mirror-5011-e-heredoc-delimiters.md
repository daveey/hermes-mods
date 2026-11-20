# Bash-compatible heredoc delimiter words and bounded alternate views

Mirror entries: 4840, 4841, 4842, 4843, 4844, 4845, 4846.

## Behavior

Heredoc parsing preserves expansion-shaped delimiter words and prevents guessed terminators from hiding later group-fed scripts. Bash-reprinted command-substitution words use bounded alternate views instead of pretending their exact printed spelling is known.

## How it works

The delimiter reader takes balanced parameter, arithmetic and backtick fragments whole without executing them and decodes supported ANSI-C/localized quote fragments. For command substitutions Bash reprints, the group walker considers possible later terminator lines, replacing each selected word/line with a collision-free synthetic plain delimiter. Rewalking consumes subsequent heredoc bodies in Bash order and retains earlier raw bodies alongside their openers. More than sixteen alternate views in executed text causes a named refusal. ANSI-C decoding truncates each fragment at its first decoded NUL while retaining following word fragments, consumes paired control-backslash operands, and handles signed-32-bit overflow escapes conservatively.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_groups.py`
- `tools/shell_heredoc.py`
- `tools/shell_heredoc_words.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Heredoc-delimiter tests compare parser and scanner behavior with harmless real-Bash markers, multiple words per opener, prior bodies, tab stripping, harmless groups and view limits. NUL/escape suites cover octal, hex, Unicode/control cases, concatenation and byte boundaries. Native Bash 3.2 outcome assertions separate version-specific execution premises from scanner guarantees.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/cron/test_lifecycle_guard_heredoc_delimiters.py`
- `tests/cron/test_lifecycle_guard_heredoc_escapes.py`
- `tests/cron/test_lifecycle_guard_heredoc_nul.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The scanner does not execute delimiter expansions or replicate all Bash pretty-printing. Exact parsing and alternate-view traversal are different contracts. Use a plain delimiter when the bounded ambiguity refusal fires; source fixture outcomes on one Bash version are not proof for every interpreter.

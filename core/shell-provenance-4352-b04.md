# Lifecycle guard: arithmetic and nested-shell input provenance

Patches: **4253, 4308–4315, 4338–4343** (4352 snapshot).

## Behavior

The guard scans escaped subscript substitutions assigned to integer-attributed variables. Descriptor duplication does not split a command at the redirection ampersand.

Input files retain their execution provenance through ordered descriptor copies, moves, closes and quote removal. Nested login/startup shells receive their own stdin and environment scope; direct commands that only read data do not become shells.

## How it works

Integer declaration recognition includes declare-family option words carrying the integer attribute. ShellWord retains lexical redirection syntax separately from decoded argv text. Redirection binding follows left-to-right command position, including leading redirections.

Nested-shell extraction walks redirected shell scripts before ordinary candidates, handles startup environment input, distinguishes runuser direct-command mode, consumes option values and recognizes unique su/runuser long-option prefixes and command operands past --. Integration follow-ups align feed, hoist and quote-coverage extraction and remove an obsolete import.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_arith_words.py`
- `cron/lifecycle_guard_groups.py`
- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard_words.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/cron/test_lifecycle_guard_arith_int_assign.py`
- `tests/cron/test_lifecycle_guard_arith_lexical.py`
- `tests/cron/test_lifecycle_guard_descriptor_dup.py`
- `tests/cron/test_lifecycle_guard_descriptor_transfer.py`
- `tests/cron/test_lifecycle_guard_feed_routing.py`
- `tests/cron/test_lifecycle_guard_quoted_redirections.py`
- `tests/cron/test_lifecycle_guard_redirection_order.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`
- `tests/cron/test_lifecycle_guard_stdin_feeds.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

The scanner is conservative, not a shell interpreter. The quoted array-subscript execution proof differs between Bash 3.2 and newer Bash; refusal remains fail-closed on both. Real-Bash fixture cases execute harmless markers, not destructive lifecycle commands. Broader shell grammars and live entry paths were not exercised here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

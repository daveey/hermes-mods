# Shell comment and continuation word boundaries

Mirror batch 6; ordinals 4045, 4046.

## Behavior

Lifecycle scanning treats a hash as a comment only at a lexical word start, preserving commands after mid-word hashes and preventing quotes/backslashes in comments from hiding subsequent lines.

## How it works

A shared quote/escape/comment scanner feeds comment stripping, logical-line splitting and continuation joining. Shlex’s broad hash commenter is disabled. Escaped separators and bare carriage return remain word content; single-quoted backslashes escape nothing. A comment ends at newline even if it contains a trailing backslash. Lexical word-start state replaces a previous-character heuristic.

## Code

Source paths touched by this feature group (including regression fixtures):

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_lines.py`
- `tests/cron/test_lifecycle_guard_comment_words.py`

Mail entries in the ordered series:

- 4045: [`9725ac50794b7028165a6b2e3dd4cd1a22f2b908`](patches/4045-daveey-lifecycle-guard-a-comments-only-at-a-word-start-a-quote-or-backslash-in-a.patch)
- 4046: [`ac1098e9cb59f6b7293a8b26d237e09f06300216`](patches/4046-fix-cron-preserve-escaped-comment-word-boundaries-daveey.patch)

## Tests

`tests/cron/test_lifecycle_guard_comment_words.py`: real Bash marker rows establish actual restart execution before asserting refusal, including mid-word hashes, quoted/spliced verbs, comment quotes, escaped separators, bare CR, fd-allocation compositions and continuation forms. Clean twins and genuine comments remain allowed; direct splitter/stripper/join assertions pin precise output.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

Continuation joining for regex scans is conservative and not a full shell parser. A quoted/escaped hash is data; removing all hash tails would reintroduce fail-open scanning.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

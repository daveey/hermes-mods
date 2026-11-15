# Lifecycle guard: shell groups, cat relays and numeric substitutions

Mirrored entries: 3841, 3842, 3843, 3846, 3874.

## Behavior

The lifecycle scanner follows stdin through a bare `cat` or `cat -`, including grouped pipeline stages, to a downstream shell. A group’s trailing redirections retain its output, but stop belonging to that group at the next operator.

The narrow `$(date +%s)` numeric producer no longer turns an arithmetic expression into an inferred shell-output sink when `date` cannot be rebound. Other producers remain conservatively executable. Shell option terminators and clustered command-string payload boundaries retain their established interpretation.

## How it works

`_cat_relays_stdin` separates operands/options from redirections and declines a relay whose fd 0 is replaced (except an explicit fd-0 self-duplication). `_LineWalk` passes the upstream feed through relaying cat variants and remembers closed-group output across the group’s redirection segment.

`_split_logical_lines` moves unchanged into `cron/lifecycle_guard_lines.py`, preserving quoted/escaped-newline handling while reducing the original module’s line count. `shell_heredoc` selects a numeric-exempt sink regex only in the absence of a conservative rebinding marker (functions, aliases, PATH/environment changes, sourcing and related commands). The option-end and rcfile changes in this batch are tests, not new parsing behavior.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_groups.py`
- `cron/lifecycle_guard_lines.py`
- `tools/shell_heredoc.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/cron/test_lifecycle_guard_group_redirections.py`: Adds allowed subshell/brace-group cases followed by semicolon, && or pipe: the next command’s redirected file is not retroactively attributed to the closed shell group.
- `tests/cron/test_lifecycle_guard_cat_relay.py`: Asserts dangerous scripts are detected through bare/option/stdin cat relays and grouped relays, while cat stages that ignore or retain their feed stay allowed.
- `tests/cron/test_lifecycle_guard_arith_sink.py`: Tests the terminal lifecycle guard with a watcher reading an oversized data log; checks several arithmetic spellings and readers remain allowed, while real command-position substitutions, other arithmetic producers, rebound date, and oversized actually executed scripts still refuse.
- `tests/cron/test_lifecycle_guard_shell_command_clusters.py`: Pins -- and lone - as option terminators before dashed script names, exact extracted command payloads, clean-script controls, and reference sets limited to the rcfile before a clustered -c payload.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

This is conservative shell analysis, not a general shell interpreter. The arithmetic exemption is only for the exact numeric date producer and is revoked when rebinding is possible. This patch’s description records a pre-existing limitation for certain substitutions after an arithmetic operator; it does not claim to close that gap. The terminal guard is exercised; no execute_code-entry parity is established here.

## Plugin?

No. Shared scanner helpers and shell dataflow must remain consistent for core guard callers.

# Lifecycle guard stdin groups and clustered shell commands

Mirror ordinals: 3751, 3758, 3780.

## Behavior

- Lifecycle inspection follows stdin files redirected onto compound commands, including nested braces, subshells and loops.
- Clustered shell command flags such as `bash -ec`, `-lc` and `-c -e` expose their inline payload for recursive scanning.
- Isolated regression tests pin compound-feed quoting, glued file descriptors and process-substitution boundaries.

## How it works

`lifecycle_guard_groups._LineWalk` assigns stable open-order ordinals to groups. A first token walk collects stdin redirects after each group's closing token; the second walk supplies that feed to commands inside the group. A group's own feed can replace a pipe without contaminating later commands. The caller provides its existing redirection splitter.

`lifecycle_guard._shell_options_end` tracks command-string mode through short-option clusters and subsequent options, while retaining shell-specific option-argument consumption and stdin mode. `_iter_shell_command_payloads` scans the selected operand and preserves the conservative older fallback for a later plain `-c` token.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_groups.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/cron/test_lifecycle_guard_compound_feeds_pins.py` — Quote-boundary, glued stdin, explicit fd, newline process-substitution and outer-pipe cases distinguish executable feeds from data.
- `tests/cron/test_lifecycle_guard_group_redirections.py` — A parameter matrix blocks lifecycle-bearing scripts through braces/subshells/conditionals/loops and glued assignments; clean explicit scripts, stdout/stderr-only redirects, quoted mentions and data consumers remain allowed.
- `tests/cron/test_lifecycle_guard_shell_command_clusters.py` — Shell/cluster/wrapper combinations scan dangerous payload scripts, fail closed on oversized scripts with a scan-cap reason, allow clean scripts and mere mentions, and return the expected extracted payload list.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

This is conservative static shell inspection, not execution or a complete shell grammar. An unreadable/oversized executable input remains a refusal; data redirection alone is not a lifecycle action.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.

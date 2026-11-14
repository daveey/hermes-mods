# Nested-shell alias scopes in lifecycle scanning

Mirror batch 6; ordinals 4015, 4016.

## Behavior

Login/interactive shells, zsh, SSH-fed shells and root/login wrappers are scanned with unknown inner alias names instead of trusting outer-session aliases. A script referenced in both inner and session scopes receives both required walks.

## How it works

An extracted collector finds nested heredoc bodies, command strings and script operands. A context manager temporarily changes alias and masking policy, preserving sticky alias definitions. Visited keys include alias-name/masking scope; any shell walk subsumes a Python-only scan but not another shell scope. Shared scan budgets still bound reads.

## Code

Source paths touched by this feature group (including regression fixtures):

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_nested_shells.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`
- `tools/shell_heredoc.py`

Mail entries in the ordered series:

- 4015: [`76e3ffa400e80611347e68b10d0b4b9cf8c159a7`](patches/4015-daveey-lifecycle-guard-walk-a-nested-startup-file-shell-s-input-with-unknown-ali.patch)
- 4016: [`8d2f341c788da0b7e9a1f76077baf6adfb4a4f62`](patches/4016-daveey-lifecycle-guard-key-visited-scripts-by-walk-scope-PR-2560-review-58978639.patch)

## Tests

`tests/cron/test_lifecycle_guard_session_aliases.py`: login/interactive/zsh/SSH/sudo, clustered command flags, nested heredocs and script operands refuse unsafe SQLite execution under empty or unrelated outer alias sets. Plain-child and quoted Python-body controls remain allowed. Shared-script reference order and payload/source variants prove a masked nested walk cannot suppress the session walk when an interpreter name is shadowed.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

The guard conservatively models unknown nested aliases; it does not execute remote startup files. Masking assumptions and scan caps remain relevant.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

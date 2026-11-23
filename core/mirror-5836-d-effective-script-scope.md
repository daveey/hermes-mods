# Lifecycle guard effective script-visit scope

Source patch ordinals: 5758–5759.

## Behavior

Recursive lifecycle scanning records the effective scope at script entry so the same script is not charged twice merely because alias/integer context was discovered during entry. Stronger later contexts and distinct working directories remain separate visits.

## How it works

`mark_effective_visit` runs after alias discovery and before recursive traversal. It pre-discovers integer declarations and records visited keys carrying execution state and, where applicable, the effective cwd. Independent aliases and genuinely stronger scope still require independent coverage.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_nested_shells.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/cron/test_lifecycle_guard_effective_scope.py` covers substitution wrappers, integer declarations, executed versus non-executed visits, later contexts, independent alias scope and distinct working directories.

## Applying

Apply both traversal plumbing and visit-key logic. Keep the existing scan budget and command policy; this is deduplication of established scope, not permission to skip newly discovered scope.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

A path-only visited set would lose cwd/execution distinctions. Recording caller scope before entry would reintroduce duplicate charges or understate the script context.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.

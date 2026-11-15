# Scratch-state, refusal-backoff and archive restore safeguards

Mirror ordinals: 3732, 3740, 3759, 3761, 3773.

## Behavior

- A clean repository with no commits and no refs holds no work that must pin a pooled card to a worker.
- Scratch refusal timestamps stay below the cross-platform formatting limit; formatting failures cannot abort the GC skip log.
- Archive restore tests preserve fail-closed validation of unexplained missing refs and detached HEAD objects.
- Test-only follow-ups reject boolean timestamps and remove a cancellation-prone float comparison from a budget assertion.

## How it works

`kanban_move_pool.tree_state` checks dirt first, then `_commit_less`: HEAD verification must return exactly 1 and a successful first-ref listing must be empty. Any other answer keeps the existing unpushed/unchecked fail-safe, using the shared scan budget. The general unpushed helper is unchanged.

`kanban_scratch_backoff` bounds finite timestamps with `MAX_EPOCH_S = 3.2e10`; `_utc` catches platform formatting exceptions and emits the raw numeric timestamp. The memo-loader matrix adds boolean epoch cases. The CLI budget test compares each deadline with its construction expression instead of subtracting large floats. Archive tests distinguish a remote explicitly reported gone from an unexplained missing tracking ref, and verify detached HEAD as a restore prerequisite.

## Code

- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/kanban_scratch_backoff.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_scratch_archive_restore_refs.py` — Only positively gone remote refs are omitted; an unaccounted missing tracking ref refuses restore, as does a partial-clone detached HEAD whose object cannot resolve.
- `tests/hermes_cli/test_kanban_scratch_gc_backoff.py` — OSError/OverflowError/ValueError from gmtime still log and skip safely; oversized and boolean last_at/retry_at records are malformed; default/explicit budget deadlines equal started plus their configured durations.
- `tests/hermes_cli/test_kanban_tree_state_unborn.py` — Clean git-init fixtures do not pin and previously fixture-pinned cards can unpin; untracked/staged files, broken refs, detached commits, other branches, failed ref listing and timed-out probes still hold state.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

An unborn HEAD alone does not establish an empty repository. The dirt check must precede the exception. Archive and GC tests manipulate disposable Git fixtures; none were executed here.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.

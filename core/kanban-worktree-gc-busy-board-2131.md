# Worktree sweep skips a busy board

Patches **2085, 2086**.

## Behavior

- The startup/cron worktree sweep opens each board with a short busy timeout. A busy board now logs one INFO ("worktree GC skipped busy board ..., retrying next sweep") and the sweep continues; previously `database is locked` aborted every remaining board with a WARNING traceback. Non-busy `OperationalError` still raises `_BoardError`, preserving the GC-abort card.
- Back-to-back busy skips per board are counted in `<kanban home>/gc-busy-skips.json` (reset on a clean pass); from 3 in a row, `kanban_gc_alert.report` opens or comments the agent card. 2086 reads that file with `utf-8-sig`, tolerating a BOM.

## How it works

`_sweep_repo` wraps each board in try/except, classifies with `_is_busy_error`, and calls `_note_busy_skip(board, cleared=...)`, which atomically rewrites the JSON via a temp file and ignores corrupt or non-dict state.

## Code

- `hermes_cli/kanban_worktree_gc.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/hermes_cli/test_kanban_worktree_teardown.py`: skips a busy board and cleans the rest; alerts once after three busy skips; non-busy mutation error still opens the abort card; a clean pass writes no state. The BOM tolerance in 2086 has no dedicated test.

## Applying

Apply ordinal(s) 2085, 2086 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Related: [kanban-worktree-retention.md](kanban-worktree-retention.md), [kanban-worker-worktree-cleanup.md](kanban-worker-worktree-cleanup.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

A board busy on every sweep is never cleaned until the alert is acted on. Counts are per board path.

## Could this be a plugin?

No. It is part of core Kanban worktree lifecycle.

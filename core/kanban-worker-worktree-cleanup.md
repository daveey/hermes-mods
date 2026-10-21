# Kanban worker guidance: remove worktrees you create

Patches:

- `patches/0162-fix-kanban-worker-guidance-pairs-every-git-worktree-.patch`: `KANBAN_GUIDANCE` pairs every `git worktree add` with a `git worktree remove` before `kanban_complete`.

What you see: Kanban workers clean up the git worktrees they make, instead of leaking them (an audit found 630 leaked worktrees, about 100 from workers).

## Behavior

- New guidance bullet: any other `git worktree add` the worker runs (nested, a sibling `~/code/<repo>-<slug>`, `$TMPDIR`) is the worker's to remove with `git worktree remove <path>` once its branch is pushed, merged or abandoned, before `kanban_complete`; the branch survives. Name in the handoff any it must leave.

## How it works

- `agent/prompt_builder.py`: one added line in `KANBAN_GUIDANCE`, after the workspace bullet.

## Code

`agent/prompt_builder.py`.

## Tests

`tests/agent/test_system_prompt.py::test_kanban_guidance_makes_the_worker_remove_worktrees_it_creates`: guidance names both commands and `kanban_complete` within 400 characters of the remove step. It checks the prompt text, not worker behavior.

## Applying

Standalone; the guidance text overlaps with 0113/0155 edits, so apply in order.

## Gotchas

- Guidance only: nothing enforces the removal.

## Plugin?

No. `KANBAN_GUIDANCE` is a core constant.

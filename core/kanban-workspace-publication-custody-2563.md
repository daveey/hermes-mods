# Git-workspace custody at Kanban handoff

Patches: **2494, 2495, 2496**.

## Behavior

Before a running card voluntarily hands off (`continue`, `block`, `schedule`, or `request_review`), core now checks whether the Git work it owns is published. A handoff is refused with `unpublished_workspace` unless every checked workspace is clean and its branch matches the live `origin` branch SHA exactly, or the checkpoint/reason carries a nonempty line `UNPUBLISHED_WORK_REASON=<why the work must remain on this host>`.

- The retention reason records explicit host custody; it does not make the card movable.
- The closed run stores server-derived receipts: `workspace_publications` (authoritative list) and `workspace_publication` (the first, for compact readers). A caller-supplied receipt in the handoff metadata is stripped, not trusted.
- Involuntary or unguarded endings (crash, timeout) record `unverified` custody with no remote probe and never block run recovery.
- Kernel goal-loop parks (budget, non-finalization, unachievable) are involuntary: they skip the guard so a stop is never refused, and record unverified custody.
- Custody carries forward across checkpoint rollover: the newest unresolved receipt per path keeps being checked even after the checkpoint prose that named it is out of the three-run history window or the worktree is deleted. An exactly published receipt releases older custody for that path.
- Nothing is staged, committed, or pushed automatically.

## How it works

`hermes_cli/kanban_workspace_publication.py` defines `publication_guard`, applied to `continue_task`, `block_task`, `schedule_task` and `request_review`. `_paths` gathers the declared Git workspace plus linked-worktree roots (a `.git` *file*) named in the checkpoint/reason/summary, metadata `worktree`/`worktrees`, or the last three run records; plain clones mentioned in prose are not inferred to be owned. `_observe` runs Git with prompts disabled (status, `ls-remote origin refs/heads/<branch>`, a repeat of the local state) under one shared five-second budget, outside the write transaction.

The result rides a context variable to `closed_metadata`, called from `_end_run` inside the transition. That function re-checks branch, HEAD and cleanliness (one-second budget) and fails with `unpublished_workspace` if the workspace changed after the probe. `_end_run` moved to the new `hermes_cli/kanban_db_run.py` (re-exported from `kanban_db`) to host this. `involuntary_park` sets a kernel-only call scope; goal-loop code in `hermes_cli/goals.py` and `kanban_goal_recovery.park` use it. Placement refusals such as `workspace_bound` are unchanged; see [kanban-worktree-retention.md](kanban-worktree-retention.md) and [kanban-published-worktree-budget-2253.md](kanban-published-worktree-budget-2253.md) for related worktree behavior.

## Code

`hermes_cli/kanban_workspace_publication.py` (new), `hermes_cli/kanban_db_run.py` (new), `hermes_cli/kanban_db.py`, `hermes_cli/kanban_continuation.py`, `hermes_cli/kanban_db_schedule.py`, `hermes_cli/goals.py`, `hermes_cli/kanban_goal_recovery.py`. The patch also adds `docs/kanban-workspace-publication.md`, which includes a design note for a future portable-source manifest (not implemented here).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests):

- `tests/hermes_cli/test_kanban_workspace_publication.py`: real Git repos with disposable bare origins; calls the DB entrypoints directly (`continue_task`, `block_task`, `schedule_task`, `request_review`). Covers unpublished/dirty/untracked/unpushed work, deleted remote branch with a stale tracking ref, exact publication, retention reason, scratch plus external linked worktree via checkpoint, metadata and prior checkpoint, crash audit, a local mutation racing the probe, and custody surviving repeated checkpoint rollover.
- `tests/hermes_cli/test_kanban_publication_recovery.py`: goal-loop kernel parks (`run_kanban_goal_loop`, `park`) record unverified custody; a published receipt releases earlier unpublished ones; forged caller receipts are stripped; a malformed retained path still allows explicit retention.

CLI, tool, REST and Desktop transports and cross-host recovery are untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Handoffs from a card with an unpublished Git workspace now fail until the work is pushed or a retention line is added; agents must do one or the other.
- The guard probes `origin` over the network (five-second budget); an unreachable remote counts as unpublished.
- Inside an open transaction the probe is skipped and the observation is `unverified`, so only an explicit retention reason lets such a call pass.
- A receipt is not a portability grant; host-bound cards stay host-bound.

## Plugin

Core change; no plugin required.

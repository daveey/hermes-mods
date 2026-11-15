# Core-update requeue keeps the retry on the host of live run processes

Mirror snapshot 4352; ordinals 4244, 4245.

Entries:

- **4244**: kanban: core-update requeue keeps the retry on the host of the run's live processes
- **4245**: kanban: requeue custody bound spares a job the custody host's running retry holds; liveness on macOS

## Behavior

A restart-safe requeue (reason: core update) reclaimed a run and made the card claimable by any pool host at once, while processes the reclaimed run had started (for example a long-running ship command) were still alive on the original host, so two runs acted on one card.

- On reclaim, processes the run started that are still alive on this host are found from the process registry (entries whose parent session is one of the card's admitted sessions, checked by kernel start time) and the run's systemd scope. Plain processes are terminated as a group (TERM, then KILL) before the lease is released; persisted jobs (`persist_on_release`) and their descendants are spared.
- If persisted jobs are alive, the reclaimed payload carries `custody: {host, run_id, run_uid, processes, until}` in the same transaction as the status flip (so it syncs together), with `until = now + max_runtime_seconds` (3600 when unset). `claim_gate` defers other hosts' claims with a `requeue_custody` reason naming host, pid and command; the custody host claims normally.
- The custody host's dispatch sweep records `requeue_custody_released {outcome: exited}` when all pids exit, or at `until` terminates them and records `{outcome: terminated}`; a custody whose host never reports stops holding 600 s after `until`.
- 4245: the bound is skipped while this host's own retry holds the card running (the designed persist_on_release case), and liveness on platforms without `/proc` uses the registry's process fingerprint (psutil start time) and reads zombies as dead.
- Other reclaim reasons and requeues with no live process are unchanged.

## How it works

- New `hermes_cli/kanban_requeue_custody.py` (`_held_here`, `_start_ticks`, sweep); hooks in `kanban_db.py` (`reclaim_task`), `kanban_db_sync.py` (`claim_gate`) and `kanban_db_dispatch.py` (sweep); `kanban_worker_scope.py` exposes a shared `checkpoint_entries()` helper.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_requeue_custody.py`
- `hermes_cli/kanban_worker_scope.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_requeue_custody.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Residual (from the commit message): a replica that applies the status flip and the reclaimed event in different sync pages could claim in between; a foreground command on a host with no systemd user bus has no run scope to find.

## Plugin?

None.

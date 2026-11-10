# Killed-subagent notice retry for busy parents

Patch **2074**.

## Behavior

- The startup orphan reap closes subagent sessions killed by a restart and writes a "subagent stopped by a restart" notice into the parent chat's live tip. If the parent is mid-turn (turn lease) or being compressed, the write is refused; this is now logged once at INFO ("parent busy, will retry") with no traceback, not as a WARNING that drops the notice.
- The kanban-unloaded sweeper re-offers the notice every pass for each child reaped within the last 24 hours whose parent lineage has no `subagent-killed:<child>` notice. Repeats are a read-only no-op because the notice id is deduplicated.
- A notice still undelivered 30 minutes after the reap logs one WARNING per child (per process); unexpected errors keep WARNING plus traceback.

## How it works

`reap_notices._deliver` writes via `record_kanban_notice` and classifies the result as `written`, `present`, `busy` (`SessionTurnLeaseLostError` / `CompressionSessionClosedError`) or `error`; it never raises. `notify_parents_of_killed_subagents` (inline path) logs busy at INFO. `retry_pending_killed_notices(db)` selects children with `end_reason='startup_orphan_reap'` inside `RETRY_WINDOW_S`, reuses `killed_subagents` to drop children whose notice already exists, delivers, refreshes the parent's outcome for newly told parents and tracks overdue warnings in a lock-guarded set. The sweeper calls it before `guarantee_pass`, so the same tick can wake the parent.

## Code

- `tui_gateway/reap_notices.py`: `_deliver`, `retry_pending_killed_notices`, `RETRY_WINDOW_S`, `OVERDUE_S`.
- `tui_gateway/kanban_unloaded.py`: sweeper loop calls the retry before the guarantee pass.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/tui_gateway/test_session_outcome_restarts.py` covers: busy parent receives the notice after its turn ends; one overdue warning past the window; an unexpected failure still warns with a traceback; old reaps and finished children are ignored; the sweeper pass runs the retry before the guarantee pass. The idle-parent inline path was already covered by the existing restart test. Desktop rendering of the notice is untested here.

## Applying

Apply ordinal(s) 2074 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- Overdue-warning state is per process and resets on restart; a still-busy parent re-warns once after restart.
- Only children reaped in the last 24 hours are retried; older losses are never re-offered.
- The retry scan runs on every sweeper pass; it is a lineage read per reaped child and takes no write lock when the notice already exists.
- Relates to [process-masked-exit-notices.md](process-masked-exit-notices.md) and [orphan-reaper-home-scope.md](orphan-reaper-home-scope.md) (same reap family).

## Could this be a plugin?

No. The reap writer, session store errors and sweeper loop are core gateway internals with no plugin hook.

# Graceful exit kill waits for foreground spawns not yet registered

Patch **0577** (source `51ae32d755`).

## Behavior

- Before: `kill_live_foreground_processes()` (the graceful path every process-exit funnel uses: gateway shutdown,
  terminal atexit, compute host) snapshotted `_live_foreground` at once. A command that had already forked its child
  but was not yet published there was missed, so its process tree was orphaned to init. On a loaded runner this made
  `test_exit_cleanup_kills_foreground_command_still_running` flake.
- Now the graceful path first waits (bounded to 5 s, `_GRACEFUL_SPAWN_WAIT_S`) until `_spawns_in_flight == 0`, then
  snapshots and kills. No fence is set, so callers may keep running; only the hard-exit (`now=True`) path fences and
  keeps its own 0.5 s budget.

## How it works

`execute()` publishes the child to `_live_foreground` only after `_run_bash` returns (Popen, start-time read, stdin
pipe). `_spawns_in_flight` already counted that window for the `now=True` path. The graceful branch now does
`_live_foreground_cond.wait_for(lambda: _spawns_in_flight == 0, _GRACEFUL_SPAWN_WAIT_S)` inside the condition lock
before taking `list(_live_foreground.values())`.

## Code

`tools/environments/base.py` (`kill_live_foreground_processes`, `_GRACEFUL_SPAWN_WAIT_S`).

## Tests

`tests/tools/test_local_interrupt_cleanup.py::test_exit_cleanup_waits_for_a_spawn_not_yet_registered` holds a spawn
between Popen and registration, then calls the graceful kill; the commit reports it fails before the fix (Mac and
Linux). The file passes on the mirror host (8 passed via `scripts/run_tests.sh`). The `now=True` path is covered by
the existing tests in the same file; no new test targets it.

## Applying

Standalone; `git am -3` 0577 on a tree that has the foreground registry (`_live_foreground`, `_spawns_in_flight`).

## Gotchas

- A wedged spawn delays exit by up to 5 s, then the kill proceeds with whatever is registered.
- Takes effect when the gateway or backend restarts.

## Plugin?

No. It is inside the terminal environment base class.

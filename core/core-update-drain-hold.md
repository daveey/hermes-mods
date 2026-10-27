# Core-update drain hold (no new Kanban claims while an update drains)

Patch **0455** (source `d6dc1fe8d7`).

## Behavior

When an external updater (in my setup, `hermes-sync`'s `core-drain` step) needs an idle host to run
`hermes update --yes`, it writes `<home>/core-update/drain-hold`. While that file is held by a live process, the
Kanban dispatcher claims nothing new. Running workers are never touched: they finish, checkpoint or continue, and the
board drains instead of refilling between update checks. When the updater removes the file (or dies), dispatch
resumes on the next tick.

Without this, a board that is kept saturated never has an idle instant, so an updater that waits for "no busy work"
defers forever.

## How it works

- `agent/core_update_drain.py`: `hold_paths()` returns the process home's `core-update/drain-hold`, plus the fleet
  root's when that is a different directory, so profile homes honor a root-level hold. `read_hold()` parses the JSON
  record `{"pid", "started_at", "reason"}`; a hold counts only while its `pid` is alive (`os.kill(pid, 0)`), so a
  crashed updater cannot freeze dispatch. An unparseable file (writer mid-write) counts for 10 minutes by mtime, then
  not. `check_drain_hold(component, logger)` returns True while a hold is active and logs once per engagement.
- `gateway/kanban_watchers_common.py::_kanban_dispatch_allowed()` checks it after the existing e-stop pause
  (`check_paused`). An older core without the module (ImportError) dispatches as before.

The updater side (writing the file, waiting for running workers and live Desktop turns, the drain bound and policy)
is outside core; any supervisor can use the same file contract.

## Code

`agent/core_update_drain.py` (new), `gateway/kanban_watchers_common.py` (`_kanban_dispatch_allowed`).

## Tests

`tests/agent/test_core_update_drain.py`:
- `test_no_hold_allows_dispatch`
- `test_live_hold_blocks_dispatch`: a hold whose pid is alive blocks `_kanban_dispatch_allowed`.
- `test_dead_writer_hold_is_ignored`
- `test_unparseable_hold_counts_only_briefly`: counts inside the grace window, not after.
- `test_profile_home_honors_fleet_root_hold`

The new-claim path is tested through `_kanban_dispatch_allowed`; the live dispatcher loop holding under a real
updater is untested here.

## Applying

Apply 0455 after the Kanban watcher patches. It only adds a guard after `check_paused`. If that function moved,
re-add the same `check_drain_hold("kanban", logger)` call wherever the dispatcher decides whether to claim.

## Gotchas

- The hold stops new claims only. It doesn't interrupt running workers or Desktop turns; the updater must wait for
  them itself.
- Hosts on a core older than this patch ignore the file. An updater has to treat such hosts as "drain can't stop
  new claims" and plan for that (bounded wait, then update over restart-safe workers).
- A hold written by a pid that is later reused by an unrelated live process keeps counting until the file is
  removed; updaters should always remove it on exit.

## Plugin?

No. The dispatcher's admission decision lives in core.

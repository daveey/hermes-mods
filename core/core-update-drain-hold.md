# Core-update drain hold (no new Kanban claims while an update drains)

Patches **0455** (source `d6dc1fe8d7`) and **0460** (source `08f65aa7e6`: the hold suppresses spawns only; wake
probes, promotion and reclaim keep running).

## Behavior

When an external updater (in my setup, `hermes-sync`'s `core-drain` step) needs an idle host to run
`hermes update --yes`, it writes `<home>/core-update/drain-hold`. While that file is held by a live process, the
Kanban dispatcher claims nothing new. Running workers are never touched: they finish, checkpoint or continue, and the
board drains instead of refilling between update checks. When the updater removes the file (or dies), dispatch
resumes on the next tick.

The hold suppresses claims only (0460). Each tick still runs the rest of the dispatcher's bookkeeping: stale-claim
reclaim, crash/timeout detection, due `wake_check` probes, timed wakes and todo -> ready promotion. A card parked on a
probe wakes on time and a chain whose parent finished advances to `ready`; it is just not claimed until the hold
lifts. The "ready queue non-empty but 0 spawned" warning stays quiet while the hold is live. The e-stop (`hermes
pause`) is different and still skips the whole tick.

Without this, a board that is kept saturated never has an idle instant, so an updater that waits for "no busy work"
defers forever.

## How it works

- `agent/core_update_drain.py`: `hold_paths()` returns the process home's `core-update/drain-hold`, plus the fleet
  root's when that is a different directory, so profile homes honor a root-level hold. `read_hold()` parses the JSON
  record `{"pid", "started_at", "reason"}`; a hold counts only while its `pid` is alive (`os.kill(pid, 0)`), so a
  crashed updater cannot freeze dispatch. An unparseable file (writer mid-write) counts for 10 minutes by mtime, then
  not. `check_drain_hold(component, logger)` returns True while a hold is active and logs once per engagement.
- `gateway/kanban_watchers_common.py::_kanban_dispatch_gate()` returns `"estop"` (`check_paused`), `"drain"`
  (`check_drain_hold`) or `None`; `_kanban_dispatch_allowed()` is `gate is None`. An older core without the module
  (ImportError) dispatches as before.
- `gateway/kanban_watchers.py` (embedded dispatcher loop, 0460): `"estop"` skips the tick; `"drain"` runs
  `dispatcher.tick_once(suppress_spawn=True)`, skips auto-decompose and health publishing, and resets the stuck-tick
  counter; `None` is the normal tick.
- `gateway/kanban_watchers_dispatcher.py`: `suppress_spawn` sets `max_spawn=0` for `dispatch_once`, so
  `_tick_spawn_budget` refuses every claim after the reclaim/wake/promotion phase has run (the same early exit the
  critical-memory-pressure guard uses).

The updater side (writing the file, waiting for running workers and live Desktop turns, the drain bound and policy)
is outside core; any supervisor can use the same file contract.

## Code

`agent/core_update_drain.py` (new), `gateway/kanban_watchers_common.py` (`_kanban_dispatch_gate`,
`_kanban_dispatch_allowed`), `gateway/kanban_watchers.py` (dispatcher loop), `gateway/kanban_watchers_dispatcher.py`
(`tick_once(suppress_spawn=)`).

## Tests

`tests/agent/test_core_update_drain.py`:
- `test_no_hold_allows_dispatch`
- `test_live_hold_blocks_dispatch`: a hold whose pid is alive blocks `_kanban_dispatch_allowed`.
- `test_dead_writer_hold_is_ignored`
- `test_unparseable_hold_counts_only_briefly`: counts inside the grace window, not after.
- `test_profile_home_honors_fleet_root_hold`
- `test_estop_and_drain_are_distinct_gates`
- `test_live_hold_runs_wake_probes_and_promotion_but_claims_nothing` (0460): one real tick of the embedded
  dispatcher loop on a real board under a live hold. A due `wake_check` probe runs and wakes its card, a todo card
  whose parent is done is promoted, and a spawnable ready card is not claimed (spawn function never called). Red on
  the 0455 loop (the probe never ran).
- `test_without_hold_the_same_tick_claims`: control, the same board claims its ready card with no hold.
- `test_estop_still_skips_the_whole_tick`: no probe, no promotion, no claim under the e-stop.

A real updater driving the hold end to end is untested here.

## Applying

Apply 0455 after the Kanban watcher patches, then 0460. 0455 only adds a guard after `check_paused`. If that function
moved, re-add the same `check_drain_hold("kanban", logger)` call wherever the dispatcher decides whether to claim.
0460 needs the dispatcher's `max_spawn` budget gate to run after the reclaim/promotion phase (upstream
`_dispatch_once_locked` order); if that order changes, a zero budget may skip bookkeeping again.

## Gotchas

- Before 0460 the hold skipped the whole tick: wake probes, promotions and reclaims stopped for its full length
  (16-17 min observed). A host on 0455 without 0460 still behaves that way.
- The hold stops new claims only. It doesn't interrupt running workers or Desktop turns; the updater must wait for
  them itself.
- Hosts on a core older than this patch ignore the file. An updater has to treat such hosts as "drain can't stop
  new claims" and plan for that (bounded wait, then update over restart-safe workers).
- A hold written by a pid that is later reused by an unrelated live process keeps counting until the file is
  removed; updaters should always remove it on exit.

## Plugin?

No. The dispatcher's admission decision lives in core.

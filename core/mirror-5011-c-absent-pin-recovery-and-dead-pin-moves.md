# Absent-pin recovery, dead-pin moves, and recovered pin-wall history

Patches: **4563, 4565–4566, 4593** (5011 snapshot).

## Behavior

A single pin absent from a fresh valid membership list can be proposed as stale-pin recovery after the existing portability/never-ran checks. The Board remains the only assignment authority and distinguishes revoked from unknown names. Malformed or failed registry fetches never look like a fresh empty set.

A queued card pinned to exactly one `machine:<x>` that a successful non-empty fresh registry shows absent or revoked can leave that pin past the ordinary move cap. The move still counts in history. Live, held, stale-telemetry, busy, running, empty-registry, and multi-pin cases stay capped. Failed refresh and retry-TTL last-good membership cannot grant the exception.

Pin reroute ignores historical wall receipts once the pool positively serves the pin again. Unknown or unreadable pool state does not disprove history.

## How it works

Placement recovery validates raw registry shape before marking a fetch successful, keeps cached last-good offline discovery, and denies absence recovery after failed refresh. Unknown-name proposals reuse the revision/cooldown refusal cache.

`kanban_move.check()` adds a `dead_pin` exception using `registry(require_fresh=True)`. The `moved` event records `dead_pin`.

`kanban_pin_reroute` keeps current walls and folds live provider evidence; recovery does not treat a future receipt deadline as a live wall.

## Code

- `hermes_cli/kanban_placement_recovery.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/kanban_worker_placement.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_pin_reroute.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_absent_pin_recovery.py`
- `tests/hermes_cli/test_kanban_recovery_guard_isolation.py`
- `tests/hermes_cli/test_kanban_stale_pin_recovery.py`
- `tests/hermes_cli/test_kanban_move.py`
- `tests/hermes_cli/test_kanban_pin_history_recovery.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Absence is a Board proposal, not local reassignment. Previously-run cards and pending owner moves still preempt stale recovery. An unreadable registry refuses `registry_unavailable` before the budget. Automatic Board recovery of inactive machines is a separate route.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

# Quiet dispatcher capability, supervisor and wake deadlines

Mirror ordinals: 3997, 3998, 3999, 4000, 4001, 4002.

## Behavior

Avoids redundant capability/config decoding, idle local supervisor drains and not-yet-due wake probe selection while retaining process/external polling and exact deadlines.

## How it works

Capability refresh retains only a not-due result behind config and registry file signatures; rollback, replacement, config changes or due time reread fresh policy, bypassing the old inner TTL. A failed refresh is not success-memoized and live refresh threads remain single-flight. Supervisor gating includes active/unwoken request rows, task/config inputs and executor liveness deadline; active requests never skip. Incoming operator traffic remains independent and its errors cannot erase committed local results. Wake checks read indexed MIN(next_at) before the probe join/policy load; recovery and pool/CI housekeeping remain outside. The schema gains idx_wake_checks_next_at; the next patch only removes a blank line to retain the facade line budget.

## Code

- `hermes_cli/kanban_capability_registry.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_supervisor_gate.py`
- `hermes_cli/kanban_supervisor_requests.py`
- `hermes_cli/kanban_wake_check.py`
- `scripts/kanban_supervisor_replay_bench.py`

## Tests

- `tests/hermes_cli/test_kanban_capability_refresh_gate.py`: Pins one quiet read, exact deadline start, config/file invalidation, rollback, disabled-to-enabled transition, single-flight/failure retry and real inner-TTL bypass.
- `tests/hermes_cli/test_kanban_supervisor_drain_gate.py`: Idle calls skip local drain/config but still apply incoming each tick. Request-only changes, holder death, expiry, terminal unwoken updates and executor refresh force work. Incoming failures retain local results; synthetic replay retains refresh stamps and rejects swallowed socket/process side-effect attempts.
- `tests/hermes_cli/test_kanban_wake_due_gate.py`: No probe join or stale policy read before due; exact next_at submits once with no intervening writes. A simulated legacy upgrade installs the covering deadline index and schema replay is idempotent.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

The supervisor replay benchmark warms both paths, includes the original policy-load composition, times callbacks only and independently records forbidden attempts. It models an empty local board without replica delivery, not a live CPU budget. Do not gate active request polling or recovery on task revision alone.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

# Scope-heartbeat freshness follows the owner's published collector period

Patches:

- `patches/1026-fix-kanban-derive-scope-heartbeat-freshness-from-the.patch`

One rule decides how long an owner's `task_scopes[].observed_at` is trusted.

## Behavior

Remote-terminal release, origin-ledger owner liveness and wait probes each trusted an owner's scope heartbeat for a fixed window (450 s, 450 s, 600 s). On the loaded hub the gap between clean scope reads reached ~440 s (default-board reads timing out at 180 s), so closed remote cards flapped "never released" in session-idle-check in both directions. The window is now `clamp(2.5 x scope_heartbeat_period_s, 450, 1800)`; the hermes-hub collector publishes the period in `gateway/info`, and an owner that publishes none keeps the 450 s floor.

## How it works

New `hermes_cli/kanban_scope_heartbeat.py` is the single rule; `kanban_origin_ledger`, `kanban_session_activity`, `kanban_session_activity_records` and `kanban_wait_replica` call it instead of their own constants.

## Code

`hermes_cli/kanban_scope_heartbeat.py`, `kanban_origin_ledger.py`, `kanban_session_activity.py`, `kanban_session_activity_records.py`, `kanban_wait_replica.py`.

## Tests

`tests/hermes_cli/test_kanban_scope_heartbeat_window.py` (8): window >= 2.5 x period, and each reader (release, projection horizon, ledger, wait probe). Tests were added or changed by these commits; they were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.

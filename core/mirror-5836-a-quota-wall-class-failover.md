# Quota-walled turns fail over within their class at any wall length (mirror 5464)

## Behavior
- `may_reroute()` drops the 6-hour floor (`WALL_REROUTE_MIN_SECONDS`). A non-explicit (router-picked) pin whose quota wall has a known future reset moves to the next route in its class (`same-class-failover` with `walled_from`, one chat notice naming the reset), whatever the wall's length.
- Explicit pins still hold; the not-deferred log reasons (`key`, `endpoint`, `explicit`, `no-target`, `no-session`) are unchanged by this patch.
- Kanban card pins (`HERMES_KANBAN_TASK` set) still return False from `may_reroute` (unchanged). Walls with no known future reset (or one already past) do not move a pin and stay quiet in the not-deferred log.
- Desktop's refused-send check (`session-busy-refusal.ts`) now also treats surface code `route_refused` as a refused send, like a busy chat. `gateway/session_wall_replay.py` changes only a comment.

## How it works
`agent/model_routing_walls.py` (rule change), `gateway/session_wall_replay.py`, and `apps/desktop/src/lib/session-busy-refusal.ts` (refused-send handback).

## Code
`agent/model_routing_walls.py`, `gateway/session_wall_replay.py`, `apps/desktop/src/lib/session-busy-refusal.ts`.

## Tests
Source: `tests/agent/test_walled_default_pin.py` (by test names: a wall under six hours moves a default pin, an unknown wall refuses quietly, a launch-key hold names why, a Desktop session behind a short wall builds on the next in-class route, a default pin moves when half the pool returns within six hours), `apps/desktop/src/lib/session-busy-refusal.test.ts`. Not executed in this mirror pass.

## Applying
Single patch.

## Gotchas
Behavior change: short walls now switch providers instead of waiting.

## plugin?
No.

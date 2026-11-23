# Dashboard thread lanes keep /api/status responsive (mirror 5465–5468)

## Behavior
- Route groups get their own bounded thread budgets so slow session/kanban reads cannot starve `/api/status` in the shared AnyIO pool. Lanes (default sizes): control (4: `/api/status` thread work, sidebar cache-hit lookup), sessions (8: `/api/sessions`, `/api/profiles/sessions`, sidebar cache misses), probe (2, with a 2 s deadline inside `/api/status`: storage probe, active-session count, FTS status), feed (6; defined and sized by config, but none of these patches routes a call site to it), kanban (4: `/api/session-kanban-tasks`, added by 5466 after 5465 first put it on the sessions lane).
- A busy probe lane leaves its status fields unknown/degraded rather than delaying the answer (per the source comments; the exact field values were not traced).
- A caller waits at most `dashboard.thread_lanes.queue_deadline_s` (25 s) for a lane thread or, for callers that pass `request=` (only `/api/session-kanban-tasks` in this diff), until its client disconnects, then receives 503 with `Retry-After` and the work never starts. Started work runs to completion. Lane threads never use the shared pool.
- Slots never leak: cancellation right after hand-off releases the slot; a caller cancelled while its thread runs keeps the slot until the thread returns; free slots are taken with `acquire_on_behalf_of_nowait`; only a caller's own cancels fail admission; `served` counts only runs that started.
- `/api/health` reports per-lane counters (`in_use`, `waiting`, `served`, `rejected`, `disconnected`, `max_wait_s`). Sizes and deadline come from `config.yaml`, read once at start-up off the loop; `configure()` is called from the standalone app lifespan via `asyncio.to_thread` and replaces the lane table in one assignment. The response also carries `queue_deadline_s` and each lane's `size`.

## How it works
`hermes_cli/web_thread_lanes.py` (`_admit`, `run_in_lane`); routers `status.py`, `sessions.py`, `profiles.py` and `web_server_app.py` call into it.

## Code
`hermes_cli/web_thread_lanes.py`, `hermes_cli/web_routers/{status,sessions,profiles}.py`, `hermes_cli/web_server_app.py`.

## Tests
Source: `tests/hermes_cli/test_web_thread_lanes.py` (by test names: a full lane makes the next caller wait; deadline 503 and the work never runs; per-call deadline shorter than the lane's; a disconnected waiter is dropped; one full lane never delays another; lanes do not use the shared default pool; `lane_runner` for coalesced reads; config and unconfigured defaults; unknown lane is an error; cancel right after hand-off or after an uncontended acquire never leaks the slot; a disconnect seen with the hand-off never leaks (with a `hand_off_first` parameter); a cancelled caller keeps the slot until its thread returns; work exception releases the slot; work cancelled before a thread picks it up never runs), `test_dashboard_thread_starvation.py` (status and health answer while slow reads fill the shared pool and the sessions lane; status does not wait out a full probe lane), and `test_profile_scan_http_admission.py`, which is touched without new test functions. Not executed in this mirror pass.

## Applying
Apply in order; later patches fix admission/leak races in the first.

## Gotchas
Exhausted lanes return 503 to clients, which must retry.

## plugin?
No.

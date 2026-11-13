# Gateway update exit bound and always-on keep-alive drain

Patches: **3223, 3226**.

## Behavior
- Gateway: `asyncio.run`'s shutdown and the outer `finally` are bounded to a core update's exit target. A post-tail bound takes the exit with the tail verdict, needs an exit target, stands down once the funnel disarmed, and cuts a wedged default-executor join. The exit funnel parks while the bound owns the exit and marks its phase; the slow-tail log carries a tail-start mark.
- Always-on web backend: a superseded backend drains keep-alive sockets before retiring. While draining, HTTP responses get `Connection: close` (an upgrade is kept); open HTTP connections hold retirement until they close, with unknown/stuck connections retiring at a cap; an aborted roll stops the drain; the drain runs before the backend asks the fence.

## How it works
- 3223 extends `gateway/exit_deadline.py` and wires `run.py` / `run_bootstrap.py`.
- 3226 adds a drain wrapper and connection counting in `hermes_cli/web_server_skew_exit.py`, hooked from `web_server.py`.

## Code
- `gateway/exit_deadline.py`
- `gateway/run.py`
- `gateway/run_bootstrap.py`
- `hermes_cli/web_server_skew_exit.py`
- `hermes_cli/web_server.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_update_exit_target.py`: `test_post_tail_bound_takes_the_exit_with_the_tail_verdict`, `test_post_tail_bound_needs_an_exit_target`, `test_post_tail_bound_stands_down_once_the_funnel_disarmed`, `test_post_tail_bound_cuts_a_wedged_default_executor_join`, `test_exit_funnel_parks_while_the_post_tail_bound_owns_the_exit`, `test_exit_funnel_parks_the_main_thread_once_the_bound_owns_the_exit`, `test_exit_funnel_marks_its_phase`, `test_slow_tail_log_carries_a_tail_start_mark`.
- `tests/hermes_cli/test_always_on_drain.py`: `test_close_headers_adds_or_replaces_connection_but_keeps_an_upgrade`, `test_wrapper_adds_close_only_to_http_while_draining`, `test_superseded_backend_drains_before_it_asks_the_fence`, `test_open_connections_hold_retirement_until_they_close`, `test_unknown_or_stuck_connections_retire_at_the_cap`, `test_an_aborted_roll_stops_the_drain`, `test_open_http_connections_counts_http_protocols_only`, `test_keep_alive_socket_closes_after_the_next_response_once_draining`.
- `tests/hermes_cli/test_always_on_rollover.py` was adjusted.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Unknown or stuck connections still retire at the cap, so a drain is bounded, not guaranteed.

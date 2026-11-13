# Session writes ride out a starting gateway

Patches: **3443, 3444**.

## Behavior
- A session write (e.g. an archive PATCH) sent while the gateway is starting no longer fails at once with a 503 telling the user to drain and stop the gateway. Owner discovery re-reads a `starting` verdict (no endpoint) read-only every 0.5 s within a 24 s budget counted from the first read; the mutation is still sent at most once and discovery never starts a gateway.
- A gateway still starting at the deadline yields a retryable 503 `owner_unavailable: starting: ... nothing was changed, retry`, without drain/stop advice.
- `absent`, draining and every refusal verdict stay final on the first read with their original details; an absent owner still edits offline.
- The budget is asserted to fit under the Desktop request deadline.

## How it works
3443 changes `hermes_cli/session_owner_discovery.py` to re-read `starting` (and a missed identify deadline once `starting` was seen) on its own budget, and to produce the new starting detail; three older tests that pinned the first-read refusal are updated. 3444 updates the standalone session-mutation test that pinned the old text and waited the real budget, so it asserts the new detail and caps its budget.

## Code
- `hermes_cli/session_owner_discovery.py`
- `tests/hermes_cli/test_session_owner_discovery_starting.py`
- `tests/hermes_cli/test_session_owner_discovery_retry.py`
- `tests/hermes_cli/test_session_maintenance_http.py`
- `tests/hermes_cli/test_web_server_session_owner_forward.py`
- `tests/gateway/test_mute_live_owner.py`
- `tests/hermes_cli/test_session_mutation_standalone.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_session_owner_discovery_starting.py`: `test_starting_is_re_read_until_the_gateway_is_ready`, `test_a_persisting_starting_stops_within_its_budget`, `test_identify_stalls_of_a_starting_gateway_are_re_read_on_the_starting_budget`, `test_the_starting_budget_counts_from_the_first_read`, `test_a_final_verdict_after_starting_ends_the_wait_at_once`, `test_only_a_starting_verdict_without_an_endpoint_is_starting`, `test_the_detail_names_starting_only_for_starting`, `test_the_starting_budget_fits_under_the_desktop_request_deadline`, `test_a_write_during_starting_sends_exactly_one_mutation_once_ready`, `test_a_gateway_still_starting_past_the_budget_answers_retry_not_drain`, `test_a_refusal_behind_a_held_lock_keeps_the_lock_remedy`, `test_an_absent_owner_still_edits_offline_on_the_first_read`, `test_a_real_reservation_without_a_socket_refuses_with_retry_then_the_retry_lands`, `test_a_write_sent_while_a_real_gateway_starts_lands_exactly_once`.
- `tests/hermes_cli/test_web_server_session_owner_forward.py`: `test_live_owner_forward_answers_the_starting_detail`.
- 3444 edits `test_session_mutation_standalone.py` without adding test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The 24 s budget was chosen from observed startup times and the 30 s Desktop request cut-off; slower starts still get the retryable 503.

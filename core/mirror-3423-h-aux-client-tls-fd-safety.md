# Aux client close safety against abandoned provider daemons

Patches: **3389, 3390, 3416**.

## Behavior
- After a hard cancel of a protected auxiliary call (a user Stop, or the core-update pause yielding compression), the per-call client is no longer closed under the abandoned provider daemon's TLS read. Previously the closed socket's fd number could be reused by the next `open()` (for example a SQLite database), and the daemon's next TLS read or alert write hit that file.
- The close is deferred to the abandoned daemon itself once it finishes (or runs on the caller if no daemon is abandoned or it already finished), and is gated on the owner's socket-shutdown sweep finishing first, so a daemon cannot close a socket the owner is still shutting down.
- The native-adapter streaming path (aggregating through `_complete`) is also wrapped in the client scope.
- A tripwire logs a close from a stranger thread under a live read; it installs only when enabled by config (`logging.debug_ssl_close`, documented in the user guide).

## How it works
- 3389 adds `agent/aux_provider_handoff.py` (`release_client`, abandoned-daemon notes), `agent/ssl_close_tripwire.py`, and routes class-routing clients through a client scope in `auxiliary_class_routing.py`/`auxiliary_client.py`.
- 3390 defers the close before the shutdown (a fast daemon could otherwise finish first and fall back to the owner thread), wraps the native-adapter branch in `client_scope`, and enables the tripwire via config.
- 3416 makes the daemon-side release wait on an event the owner sets right after its sweep (in a `finally`, so a failing sweep still releases daemons), and documents the config option and docs-drift baseline.

## Code
- `agent/aux_provider_handoff.py`
- `agent/auxiliary_class_routing.py`
- `agent/auxiliary_client.py`
- `agent/ssl_close_tripwire.py`
- `hermes_logging.py`
- `hermes_cli/config_defaults.py`
- `website/docs/user-guide/configuration.md`
- `scripts/ci/docs_drift_baseline.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_aux_abandoned_provider_tls_fd.py`: `test_cancelled_class_call_never_hands_the_daemons_tls_fd_to_sqlite`, `test_release_without_abandoned_daemon_closes_on_the_caller`, `test_release_runs_on_the_abandoned_daemon_once_it_finishes`, `test_release_closes_at_once_when_the_daemon_already_finished`, `test_note_abandoned_outside_a_scope_is_a_no_op`, `test_tripwire_logs_a_stranger_thread_close_under_a_live_read`, `test_tripwire_is_quiet_for_an_owner_close`, `test_tripwire_installs_only_when_enabled`, `test_daemon_finishing_during_the_owner_sweep_closes_only_after_it`, `test_a_failing_owner_sweep_still_releases_the_gated_daemon`, `test_mixed_finished_and_reading_daemons_close_once_on_the_last_reader`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The source message for 3390 reports the end-to-end test was flaky under load before the defer-first ordering; mirror did not re-run it.

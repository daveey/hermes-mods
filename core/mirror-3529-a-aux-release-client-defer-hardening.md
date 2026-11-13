# Aux release_client defer hardening and SSL-close config test

Patches: **3505**.

## Behavior
- If a later `defer()` raises (including a KeyboardInterrupt on the owner thread) after an earlier one succeeded, the already-deferred daemon no longer waits forever: the client still closes on the first daemon's thread.
- `logging.debug_ssl_close` read from config.yaml installs the SSL close tripwire in `setup_logging` for true / "on" and not for absent / false (test-only change).

## How it works
3505 moves the defer calls in `release_client` (`agent/aux_provider_handoff.py`) inside the try, counts them one by one, and has the finally open the `swept` gate and count down every attempt that took no share. The logging part adds a test only.

## Code
- `agent/aux_provider_handoff.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_aux_abandoned_provider_tls_fd.py`: `test_a_defer_raising_after_another_succeeded_still_closes_on_the_first_daemon`.
- `tests/test_hermes_logging.py`: `test_debug_ssl_close_from_config_yaml_installs_the_tripwire` (parametrized).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

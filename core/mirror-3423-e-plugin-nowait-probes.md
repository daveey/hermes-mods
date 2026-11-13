# Plugin `*_nowait` probes never wait on a held sweep

Patches: **3220, 3234**.

## Behavior
- The plugin `*_nowait` probes return the last live set while another thread holds the reload lock, serve the persisted set if never answered live, and never block the event loop when no set is known.
- Off the loop, a never-discovered process blocks to sweep; on the loop, an idle never-discovered process sweeps inline (3234), and after a failed sweep the on-loop call skips the retry.
- The empty on-loop fallback is documented and logged, and not logged when a persisted set was served.

## How it works
Both patches edit `hermes_cli/plugins.py`: 3220 adds the non-blocking lock handling and fallback ladder; 3234 adds documentation, a log line for the empty fallback, and the inline idle sweep.

## Code
- `hermes_cli/plugins.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_plugins_nowait_reload_nonblocking.py`: `test_nowait_returns_last_live_set_while_reload_holds_lock`, `test_nowait_serves_persisted_set_when_never_answered_live`, `test_nowait_never_blocks_event_loop_without_any_known_set`, `test_nowait_blocks_off_loop_for_never_discovered_process`, `test_nowait_on_loop_skips_retry_after_failed_sweep`, `test_nowait_empty_fallback_not_logged_when_persisted_set_served`, `test_nowait_idle_never_discovered_sweeps_inline_on_loop`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- On the event loop with no known set, the probe can return an empty set.

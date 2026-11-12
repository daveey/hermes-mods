# Cron owner shutdown survives the drain ending

Patches: **2849, 2885, 2903**.

## Behavior

An agent cron run stopped by its owner gateway’s restart is reported as a shutdown interruption, not a lost-ownership failure. The shutdown reason and restart-cut bookkeeping survive the run unwinding after the drain flags have been reset.

## How it works

- **2849:** `ShutdownAwareCancel` recognizes the owner runner’s drain when no operator cancellation marker exists, producing shutdown warning wording instead of a failed-job traceback.
- **2885:** `_restart_cut` reads the sticky `_interrupting_remaining_work` flag before `_draining`; the reason predicate likewise recognizes that interrupt latch after stop has finished.
- **2903:** `latch_stop` stamps `owner_shutdown` on a previously unset cancel event at stop time. Both `stop_authority_work` and forwarded `cron.cancel` use it. `_latched_owner_shutdown` supplies the reason and restart-cut classification after mutable runner state disappears. An already-set latch keeps its original meaning.

## Code

- `gateway/session_cron.py`
- `gateway/session_runtime_workers.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

The owner-drain cases in `test_shutdown_interrupt.py` cover warning/no traceback, operator Stop, non-drain takeover and reset-before-poll. `test_restart_cut.py` covers a return after stop completes. `test_session_cron.py` drives a forwarded cancellation and verifies the restart-cut result flag after clearing drain state.

- `tests/cron/test_shutdown_interrupt.py`
- `tests/cron/test_restart_cut.py`
- `tests/gateway/test_session_cron.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

An operator cancel marker remains authoritative for shutdown-reason classification. A genuine takeover without an owner drain must retain the lost-ownership error. This is not a blanket suppression of cron failures.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

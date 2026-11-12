# Re-adopted Desktop jobs: completion wake and late-orphan retry

Patches: **2466–2468**.

## Behavior

After a Desktop backend restart, a file-backed (`persist_on_release`) background job that the new backend re-adopts now reports its real exit and output to the owning chat through the Desktop wake sink, even when that chat is not loaded, and delivers exactly one completion turn. Gateway-owned adopted jobs keep the existing queue path and do not use the Desktop sink. Separately, when an overlapping backend (the previous writer of the process checkpoint) is still alive at startup, the replacement no longer strands that job: the Desktop checkpoint keeper retries adoption on each tick, so an orphan whose writer exits later is picked up, including a job that finished while unowned (its log and exit trap survive).

## How it works

`ProcessCheckpointMixin` in `process_registry_checkpoint` sets `session._completion_via_readopt_sink = owner == OWNER_TUI` for re-adopted sessions, the same flag re-attached sandbox jobs already use, so the exit goes to `registry.on_unknown_exit` and then the TUI gateway's `_readopted_exit_deliver`, which parks the event for the chat. In `process_registry_readopt`, `_checkpoint_keeper_tick` runs `registry.reassert_checkpoint()` and then `recover_tui_orphaned_processes(registry)` every keeper interval (`start_checkpoint_keeper`); recovery keeps the live-writer and Desktop-owner fences, so it does nothing while the previous writer lives or on a non-Desktop backend. Background: [process-readoption.md](process-readoption.md).

## Code

`tools/process_registry_checkpoint.py`, `tools/process_registry_readopt.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_process_readopt_output_log.py`, `tests/tools/test_process_checkpoint_keeper_recovery.py`. They cover `ProcessRegistry.recover_from_checkpoint` for TUI and gateway owners with and without notify, the keeper tick for desktop and non-desktop backends, and an end-to-end case where a real producer interpreter writes the checkpoint and exits and the replacement dispatches one completion turn through the TUI gateway's wake, parking and batch handlers (only the model-turn submit is stubbed; that test is POSIX-only). Other entry paths are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Recovery on the keeper tick happens only on Desktop-owned backends, and only once the previous writer has exited; until then the job stays unadopted. The delivered exit may report an unknown status for jobs without a recorded exit file, while file-backed jobs report the real exit code. Patch 2467 and the later test edit in 2468 add a bounded shell loop so a test job cannot run forever.

## Plugin

Core change; no plugin required.

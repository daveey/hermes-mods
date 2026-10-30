# Managed workers, bound config and cron/process fixes after the merge

Patches: 0988, 0990, 1099, 1125, 0992, 0991.

## Behavior

- A managed worker's `RuntimeSessionStore` answers the fork's `session_db` calls: `update_session_model`, `_session_turn_lease_key`, and the `contended_wait_seconds` / `patience_s` keywords (988).
- A lease-key request for a session outside the worker's lineage returns an opaque key that matches nothing (988).
- `global_context_files` takes its setting from `worker_config_snapshot()` when a worker is bound to an explicit config, never from the profile's `config.yaml` (990).
- Import-time home initialisation is skipped when a worker config snapshot is bound, so a safe worker never reads `SOUL.md` at import (1099).
- Fork `config.yaml` readers (`prompt_builder`, kanban capabilities / assignment routing / db sync, `session_listing`, `process_consumer_health`, `conversation_compression`) go through `config_backend` (1125).
- Process-checkpoint recovery no longer deadlocks on its own `processes.json` flock: the lock is reentrant per thread (992).
- A gated cron tick (`_agent_skipped`) keeps its verdict across the canonical owner by being in `RUN_JOB_FLAGS`, so a silent tick between failing wakes no longer resets `failure_streak` or the alert-once marker (991).

## How it works

988 adds receipt ops `session.model` and `session.lease_key` served from `hermes_state_worker_lifecycle.py`. 990 branches on a bound snapshot before any disk read. 1099 wraps home init in `_ensure_home_at_import()` which returns early when `_worker_config_snapshot()` is set; it also gives the termux assembly-permissions CI job `ref: ${{ inputs.commit }}`. 1125 swaps direct open/stat of `config.yaml` for the config-backend seam to satisfy upstream's `check_config_yaml_readers` guard (plus ruff RUF100/PIE807 cleanups). 992 makes the checkpoint flock reentrant because upstream's `_track_started` writes the checkpoint while the fork already holds the lock. 991 adds `_agent_skipped` to `RUN_JOB_FLAGS` in `gateway/session_cron.py` and routes fork cron tests through upstream's `cron_owner` seam.

## Code

- `agent/runtime_session_store.py`, `hermes_state_worker_lifecycle.py` (988)
- `agent/prompt_builder.py`, `agent/conversation_compression.py` (990, 1125)
- `hermes_cli/config.py`, `.github/workflows/tests.yml` (1099)
- `hermes_cli/kanban_assignment_routing.py`, `kanban_capabilities.py`, `kanban_db_sync.py`, `session_listing.py`, `tui_gateway/process_consumer_health.py` (1125)
- `tools/process_registry_checkpoint.py` (992)
- `gateway/session_cron.py` (991)

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. 988: `tests/agent/test_runtime_session_store_surface_parity.py::test_update_session_model_writes_like_the_owner` and `::test_lease_key_answers_only_inside_the_workers_lineage`. 990: `tests/agent/test_global_context_files.py::test_worker_bound_to_an_explicit_config_never_reads_the_profile_file`. 991: `tests/cron/test_silent_tick_keeps_streak.py` (edited), `tests/cron/test_cron_finalize_slow_write.py::test_run_job_survives_title_write_timeout_and_tears_agent_down` and `::test_run_job_hands_agent_off_when_finalize_raises` (now take an `owner_bound` fixture). 992's message cites upstream `test_managed_worker_background_output` as red before and green after; no new test is added. 1099's message cites `tests/agent/test_safe_worker_policy.py` and `tests/ci/test_stable_release_commit_threading.py`; 1125 adds no new test names.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

The 992 lock is reentrant per thread only, not across processes. 991 only protects the flag path through `RUN_JOB_FLAGS`; a new run-side flag needs the same entry. 1099 leaves non-worker imports unchanged. Wording of the 990/1099 claims is limited to the cases named in the tests above.

## Could this be a plugin instead?

No: these fix fork-owned runtime internals (worker store, config seam, cron bookkeeping, process registry).

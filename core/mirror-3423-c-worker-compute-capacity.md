# Worker compute containment and admission

Patches: **3065, 3066, 3067, 3069, 3070, 3071, 3072, 3073, 3074, 3075, 3076**.

## Behavior
- Worker launches are contained by compute capacity: load-brake thresholds, a card CPU slice and memory pool, and refusal when pressure data or the service bus is unavailable or invalid.
- Managed interpreters verify their CPU budget (cpuset-based effective cores, CPU weight readback, low-priority children) before publishing an owner; failures refuse and reap.
- Worker claims are gated on sustained load and detached cgroup occupancy, with hysteresis and fail-closed handling of bad state; a worker's own grant is not double counted.
- Admission is serialized across installation roots through an account lock; idle worker boards can be retired under admission fences with crash recovery; capacity is bound to the execution database identity; same-owner resumes are allowed at capacity.
- An independent sustained-load observer writes a durable, deduplicated alert outbox.
- Legacy worker ownership is bound to a live cgroup identity.

## How it works
- 3065 adds `worker_compute_capacity.py` and occupied-owner accounting; 3066 adds launch-time verification; 3067 adds `worker_resource_admission.py` and the claim gate.
- 3069 binds policy to the account root. 3070 adds `worker_account_boards.py` with the cross-installation lock.
- 3071 and 3072 add idle-board retirement (`worker_board_retirement.py`), crash recovery and bounded pressure contention, with docs.
- 3073 binds capacity to the pinned execution DB; 3074 adds `worker_load_observer.py` and `worker_load_snapshot.py`; 3075 adds `worker_legacy_scopes.py`.
- 3076 satisfies code-health and cross-platform lint gates.

## Code
- `hermes_cli/worker_compute_capacity.py`
- `hermes_cli/worker_resource_admission.py`
- `hermes_cli/worker_account_boards.py`
- `hermes_cli/worker_board_retirement.py`
- `hermes_cli/worker_load_observer.py`
- `hermes_cli/worker_load_snapshot.py`
- `hermes_cli/worker_legacy_scopes.py`
- `hermes_cli/kanban_admission.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/kanban_db_dispatch.py`
- `gateway/session_kanban_scope.py`
- `docs/worker-compute-capacity.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_worker_compute_capacity.py`: `test_load_brake_thresholds`, `test_invalid_pressure_is_not_free_capacity`, `test_parked_live_owner_reduces_capacity_and_retries_deduplicate`, `test_unknown_populated_scope_reserves_and_failed_sample_refuses`.
- `tests/hermes_cli/test_worker_compute_launch.py`: `test_persisted_worker_job_refuses_unavailable_bus`, `test_worker_launch_retains_memory_pool_and_uses_card_cpu_slice`.
- `tests/hermes_cli/test_worker_compute_configuration.py`: `test_effective_cores_uses_host_cpuset_not_per_card_quota`, `test_configure_verifies_and_publishes_owner`, `test_cpu_weight_readback_required`, `test_failed_systemctl_never_publishes_owner`, `test_managed_owner_requires_absolute_db`.
- `tests/hermes_cli/test_worker_resource_admission.py`: `test_final_gate_counts_new_detached_jobs_and_releases_grant`, `test_worker_own_grant_echo_does_not_take_extra_slot`, `test_persistent_hysteresis_and_bad_state_refuse`, `test_pressure_contention_timeout_preserves_state`.
- `tests/hermes_cli/test_worker_account_admission.py`: `test_two_installations_share_account_lock_through_commit`, `test_same_owner_resume_at_capacity`.
- `tests/hermes_cli/test_worker_board_retirement.py`: `test_public_remove_retires_registration`, `test_inflight_writer_refuses_removal`; `tests/hermes_cli/test_worker_retirement_recovery.py` covers crash recovery.
- `tests/hermes_cli/test_worker_load_observer.py`: `test_threshold_continuity_restart_and_single_alert`, `test_failed_publish_retries_same_outbox_even_after_load_recovers`.
- `tests/hermes_cli/test_worker_legacy_scopes.py`: `test_register_binds_owner_and_lookup`, `test_register_refuses_unsafe_scope`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Several paths fail closed on unreadable pressure, a missing bus or a corrupt inventory, so a misconfigured host refuses worker launches rather than running them uncontained.

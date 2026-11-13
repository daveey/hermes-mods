# Kanban hub CPU alert threshold and OOM attribution

Patches: **3275–3276**.

## Behavior
- The hub-CPU alert treats a sampler sample as stale after the same 10 s limit the pre-lease advisory uses (previously 15 s), so a sampler stuck 10–15 s behind now files the stale-advisory card instead of silently starting nothing. A 12 s old sample trips the alert; fresh-within-limit never files.
- Deferred-claim wording names who deferred the claim.
- After a dispatcher restart, a worker killed by the kernel OOM killer is still found as the OOM victim (restricted to the worker pid), so the failure is booked as OOM and the retry's memory cap is doubled.
- The kernel-log read is bounded by the run's start instead of the last 24 h.
- A foreground command is no longer blamed for a concurrent job's OOM kill; wording is softened when the kernel has no record, a pipeline masking an OOM kill is still named, and outside a Kanban run scope nothing is read.

## How it works
- 3275 reads `kanban_admission.PRELEASE_MAX_AGE_MS` in both the advisory and the alert, with docstring fixes.
- 3276 makes `read_failure` look up the victim whenever the pid is known; `journalctl -S @<run start>` bounds the read; `OomWatch` reads the kernel's victim since the command started.

## Code
- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_hub_cpu_alert.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_worker_scope.py`
- `tools/terminal_oom.py`
- `tools/process_registry_persist_scope.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_hub_cpu_gate_contention.py`: `test_contended_census_fits_inside_the_hold`.
- `tests/hermes_cli/test_kanban_dispatch_skip.py`: `test_claim_deferred_words_name_who_deferred_it`.
- `tests/hermes_cli/test_kanban_hub_cpu_alert.py`: `test_fresh_within_the_prelease_limit_never_files`, `test_a_sample_stuck_twelve_seconds_old_trips_the_alert`.
- `tests/hermes_cli/test_kanban_oom_policy_continue.py`: `test_fresh_dispatcher_still_finds_the_agent_as_oom_victim`, `test_kernel_log_read_is_bounded_by_the_run_start`, `test_classify_dead_worker_passes_the_run_start`, `test_compute_policy_nice_wrapper_keeps_the_oom_policy_env`.
- `tests/tools/test_terminal_oom.py`: `test_oom_killed_foreground_command_names_the_run_cap`, `test_concurrent_victim_is_not_pinned_on_the_command`, `test_no_kernel_record_softens_the_sigkill_wording`, `test_survivable_command_in_its_own_scope_gets_the_note`, `test_sigkill_without_an_oom_kill_keeps_the_generic_note`, `test_pipeline_masking_an_oom_kill_is_still_named`, `test_outside_a_kanban_run_scope_nothing_is_read`, `test_kernel_victim_reads_the_journal_from_the_watch_start`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Back-to-back local claims within one sampler period share a single pre-claim sample; only the in-progress census protects CPU between them (documented in 3275).

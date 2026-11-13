# Process registry: adopted jobs record their real exit

Patches: **3654**.

## Behavior
- A background job re-adopted after a restart reports its real exit code, including jobs that `exec` another program, set their own EXIT trap, or use heredocs.
- A job killed by a signal, or any adopted job with no recorded status, settles as `unknown` / `exit_not_recorded` instead of `lost` / -1 / `backend_lost` or a false 0. Such a session is no longer pruned from its own chat's poll as an uncollected detach.
- When the local registry answers `not_found`, the owning chat's poll, log, wait and kill reach a live host-PID job that a different live Hermes process adopted. Other chats still get `not_found`.

## How it works
3654 changes `record_exit_command` so the user command runs in a `( ... )` child of a recording shell; the outer shell writes the child's `$?` and exits with it, so `exec` or a user trap cannot remove the recorder. A shell killed by a signal records nothing. The log-backed adopter maps a missing status to `unknown`. New `process_registry_foreign.py` serves poll/log/wait/kill from the shared checkpoint entry when the entry's `writer_pid` is a different live process, the job is sandbox-visible, and the caller owns it (same compression-lineage rule as retained results). It never takes the job over: the supervisor still reads the log, records the exit and sends the single completion notice. kill signals the start-time-verified tree. `_list_processes` moved to `process_registry_listing.py` to stay within a size budget.

## Code
- `tools/process_registry.py`
- `tools/process_registry_foreign.py`
- `tools/process_registry_listing.py`
- `tools/process_registry_output_log.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_process_restart_adoption_matrix.py`: `test_adopted_job_reports_its_real_exit_code`, `test_signalled_job_reports_status_unknown_not_lost_or_zero`, `test_owner_chat_polls_logs_and_waits_on_a_job_another_process_adopted`, `test_owner_chat_kills_a_job_another_process_adopted`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The foreign path applies only to host-PID jobs with a live, different supervisor; sandboxed jobs and dead supervisors are not covered by the listed tests.

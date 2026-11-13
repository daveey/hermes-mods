# OOM-killed command takes the command, not the run

Patches: **3166, 3167**.

## Behavior
- A Kanban run scope is launched with `OOMPolicy=continue` when the host's systemd accepts it, so the kernel killing one runaway tool command at the memory cap no longer stops the whole scope and loses the run. On older systemd the old argv is kept and a message is logged once.
- A foreground command killed for OOM is reported as such, naming the run's memory cap and (under the stop policy) the kernel victim; a SIGKILL without an OOM kill keeps the generic note. If the worker itself is OOM-killed the run still fails.

## How it works
- 3166 probes systemd once for `OOMPolicy` support (cached; unrelated probe failures are not cached; non-scope argv is never probed), adds the policy to dispatch argv, and adds `terminal_oom` to parse the kernel victim for this unit only and describe it.
- 3167 allowlists the probe's `systemd-run` lookup in the resolution guard fixture.

## Code
- `hermes_cli/kanban_worker_scope.py`, `hermes_cli/kanban_db_dispatch.py`
- `tools/terminal_oom.py`, `tools/terminal_tool.py`, `tools/terminal_tool_result.py`, `tools/process_registry_persist_scope.py`
- `tests/fixtures/resolution_allowlist.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_oom_policy_continue.py`: `test_argv_carries_oom_policy_continue_when_probe_accepts_it`, `test_old_systemd_keeps_todays_argv_and_logs_once`, `test_oom_policy_emitted_exactly_when_systemd_accepts_it`, `test_generic_scope_probe_and_argv_stay_free_of_oom_policy`, `test_live_child_oom_kills_scope_unless_continue`, `test_unrelated_probe_failure_is_not_cached`, `test_non_scope_argv_is_never_probed`, `test_dispatch_argv_gets_the_policy`.
- `tests/tools/test_terminal_oom.py`: `test_kernel_victim_is_parsed_for_this_unit_only`, `test_describe_never_prints_a_peak_below_the_limit_for_an_oom_kill`, `test_stop_policy_oom_names_the_kernel_victim`, `test_continue_policy_child_oom_does_not_fail_the_run`, `test_continue_policy_worker_itself_oom_killed_still_fails`, `test_oom_killed_foreground_command_names_the_run_cap`, `test_sigkill_without_an_oom_kill_keeps_the_generic_note`, `test_pipeline_masking_an_oom_kill_is_still_named`, `test_outside_a_kanban_run_scope_nothing_is_read`.
- `tests/hermes_cli/test_kanban_crash_outcome_consumers.py`, `test_kanban_gateway_restart_handoff.py`: changed; no new test names read.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Systemd versions without `OOMPolicy` keep the old behavior (whole scope stops on OOM).

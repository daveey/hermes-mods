# Kanban and terminal OOM attribution follow-ups

Patches: **3378, 3406**.

## Behavior
- A worker that exited cleanly (rc 0) no longer triggers a per-pid kernel-log lookup during reclaim; the exit is classified first and the known code is passed on.
- The worker log is read once on reclaim rather than twice inside the write transaction.
- The terminal's own-scope OOM note now fires for survivable commands whose pipeline member was killed even at rc 0, and uses firm "killed by the OOM killer" wording only for kill exits (SIGKILL 137/-9, and SIGTERM 143/-15 as sent by the service manager); other non-zero exits get softer pipeline wording.
- `OomWatch` documents that attribution is approximate (judged against a start-time cgroup snapshot; the kernel lookup returns only the window's last victim).

## How it works
- 3378 gives `read_failure` the dead worker's known exit code, adds the pure `_classify_dead_worker_exit` ahead of classification in `kanban_db_dispatch.py`, and updates `_own_scope_note` in `tools/terminal_oom.py`.
- 3406 lets `_classify_dead_worker` and `_classify_dead_worker_exit` take an optional `log=` so the reclaim passes the log it already holds, and splits the own-scope note wording by kill exit.

## Code
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_worker_scope.py`
- `tools/terminal_oom.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- Across `tests/hermes_cli/test_kanban_oom_policy_continue.py` and `tests/tools/test_terminal_oom.py`: `test_clean_exit_never_reads_the_kernel_log`, `test_classify_dead_worker_passes_the_known_exit_code`, `test_survivable_command_masking_its_own_oom_kill_is_still_named`, `test_failure_path_reads_the_worker_log_once`, `test_survivable_non_kill_exit_gets_the_pipeline_wording`, `test_survivable_kill_exit_keeps_the_firm_wording`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Attribution remains approximate by design (see Behavior).

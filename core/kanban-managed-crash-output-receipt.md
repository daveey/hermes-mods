# Kanban: crash output and owner refusals are attributed to the exact managed run

Patches: **1535 1536 1537 1538**

- `patches/1535-fix-kanban-attribute-crash-output-to-the-exact-manag.patch` (fork `fc1ad576793e`)
- `patches/1536-refactor-kanban-keep-crash-output-selection-in-log-h.patch` (fork `c185ad7a11ac`)
- `patches/1537-fix-kanban-book-the-managed-submitter-s-refusal-as-i.patch` (fork `fb794abb7166`)
- `patches/1538-test-kanban-verify-refusal-receipt-failure-preserves.patch` (fork `8688b266362e`)

This series extends [Kanban owner_restart_lost](kanban-owner-restart-lost.md) (1340–1342). That feature reads the
worker's output to decide whether a run died because its owner died.

## Behavior

- **1535: crash output comes from the run's own receipt.** When the dispatcher diagnoses a dead **managed** worker,
  the text quoted in `task_runs.error` and in the `crashed` event's `worker_output` now comes from that run's own
  `worker_result` receipt, not from the shared task log. Previously, a submitter from an older run could still
  write its reply into the shared log after the new run's start marker, so the old text was blamed on the new run.
  - If the run has a `worker_bound` event but no matching receipt, the output is empty: the shared log is not
    trusted for a bound run.
  - Legacy or unbound runs, and calls without a connection or run id, still fall back to the log reader.
- **1536** moves that selection into `kanban_worker_log.crash_worker_output`. There is no behavior change. The
  legacy log reader is still reached through `kanban_db_dispatch._worker_final_output`, so existing test
  monkeypatches of that name still apply.
- **1537: the submitter books the owner's refusal as the run's receipt.** When the gateway owner refuses the
  managed submitter (`kanban_worker_client`), for example with
  `Kanban owner refused: Gateway inaccessible: service_identity_unverified`, the submitter prints the line as
  before. It now also writes that line as the run's `worker_result` under its own pid (`tasks.worker_pid`), with
  `submitter: true`.
  - Without this receipt, 1535 left the refusal text unattributed, the owner-death classifier missed it, and the
    card was charged a failure. Now the dead-worker sweep books `owner_restart_lost`, uncharged.
  - The write is fenced to the exact attempt: the task must still be `running` with this run, this claim and this
    pid, and the run row must agree. A late submitter from an older run writes nothing.
  - A receipt the interpreter already wrote for the run is kept. For example, an exit-75 quota wall stays
    `rate_limited`.
- **1538: a failed receipt write never changes the exit code.** If booking the receipt fails, the submitter keeps
  the refusal's exit code and prints only `Kanban refusal receipt could not be recorded: <ExceptionType>`. The
  exception message, which may contain database details, is not printed. Before 1538 the failure was silently
  ignored.

## How it works

- **Reading a run's output.**
  - `managed_worker_output(conn, task_id, run_id, pid, claim_lock)` reads on the reclaim transaction's own
    connection; opening another connection could deadlock.
  - The accepted pids are the dead worker's pid, plus the interpreter pid from the run's newest `worker_bound`
    event when that event has the same `claim_lock`.
  - It scans the run's `worker_result` events, newest first, for one whose `claim_lock` matches and whose `pid` is
    one of those pids.
  - It returns that receipt's `last_output` (last 400 characters). With no such receipt it returns `""` if the run
    has a `worker_bound` event, otherwise `None`.
  - 1537 factors the scan into `_run_receipt_output`, which returns `(bound, output)`.
- **`crash_worker_output`** (1536) returns the managed output when it is not `None`, and otherwise calls
  `dispatch._worker_final_output`. `_classify_dead_worker` calls it whenever the run is not rate-limited.
- **`record_submitter_result(conn, params, pid, exit_code, last_output)`** (1537):
  - Requires `tasks(status, current_run_id, claim_lock, worker_pid) == ('running', run_id, claim_lock, pid)` and
    `task_runs(status, claim_lock, task_id) == ('running', claim_lock, task_id)`.
  - Writes nothing if the run already has a matching receipt.
  - Otherwise appends `worker_result {pid, claim_lock, exit_code, last_output[-500:], submitter: true}`.
  - The caller holds `write_txn`. It returns whether it wrote.
- **Submitter side.** In `kanban_worker_client._submit`, the `(KeyError, ValueError, OSError)` refusal branch builds
  the exit code (1, or the owner-unavailable code), then calls `_book_refusal`. That opens the board DB with
  `connect_closing` and `write_txn`, and calls `record_submitter_result` with `os.getpid()`.
  - Other exits do not book a receipt: a clean finish, or an owner restart (`KANBAN_OWNER_RESTART_EXIT_CODE`).

## Code

- `hermes_cli/kanban_worker_log.py`: `managed_worker_output`, `_run_receipt_output`, `crash_worker_output` and
  `record_submitter_result`.
- `hermes_cli/kanban_db_dispatch.py`: `_classify_dead_worker` and its import.
- `hermes_cli/kanban_worker_client.py`: `_book_refusal` and `_submit`.

## Tests

The patches add or change these tests. **They were read from the diffs and not executed in this mirror pass.**

- **1535**, `tests/hermes_cli/test_kanban_managed_result_pid.py::test_crash_output_ignores_late_prior_run_result`:
  a 3×3 parametrization, with the run's reply being a routing error, `""` or none, and the late receipt coming from
  the old run, the wrong pid or the wrong claim. A second claim's crash never quotes `OLD REPLY`, even though it
  is also in the shared log. The error and the `worker_output` payload equal the run's own reply, or empty.
- **1537**, `tests/hermes_cli/test_kanban_owner_death.py`. The `_claim` fixture now books refusals through the
  production `record_submitter_result`, keyed on the submitter pid, and its `worker_bound` event carries
  `claim_lock`.
  - `test_the_refused_submitter_books_its_own_run_qualified_receipt`: the real `main()` with a mocked
    `GatewayClientError` refusal returns 1, prints the refusal and writes exactly one qualified receipt.
  - `test_a_real_submitter_refusal_at_an_owner_death_requeues_uncounted_end_to_end`: `main()`, then the receipt,
    then `detect_crashed_workers`. The card is `ready` with 0 failures, `owner_restart_lost` carries
    `worker_output == REFUSAL`, and the older run's log text is absent.
  - `test_a_bound_run_with_no_receipt_does_not_trust_the_refusal_in_the_shared_log`: with the refusal only in the
    log, the death is charged as a crash and no output is quoted.
  - `test_a_late_refusal_from_run_a_never_lands_on_run_b`: three fenced writes with run A's details all write
    nothing; run B's own refusal is its receipt.
  - `test_the_submitter_never_supersedes_the_interpreters_receipt`: an interpreter exit-75 receipt is kept and the
    run is `rate_limited`.
- **1538**, `test_refusal_receipt_store_failure_preserves_exit_without_leaking_details`: when
  `record_submitter_result` raises `PermissionError`, `main()` still returns 1, stderr has the refusal and the
  type-only message but not the exception text, the exit trailer is `...1`, and no receipt is stored.
- **1536** has no test of its own. It is a pure move, covered by the 1535 and 1537 tests above.

**Untested:**

- The owner-unavailable exit code path through `_book_refusal`.
- A `worker_bound` event whose `claim_lock` differs from the claimer's, so its interpreter pid is ignored.

## Applying

Apply 1535, 1536, 1537 and 1538 in order (`git am -3 --empty=keep`), on a series that already contains 1340–1342
(owner death), the `worker_bound` / `worker_result` receipts, and `kanban_worker_client`. No config is needed.

## Gotchas

- **Bound runs with no receipt now show no output.** A managed run that has a `worker_bound` event but no receipt
  now has empty `worker_output`, where earlier builds quoted whatever the log tail held. Diagnostics that depended
  on log-only text from the interpreter need it written as a receipt.
- **Only refusals are booked.** The submitter books a receipt only on the refusal branch. A submitter that dies
  any other way still leaves no receipt.
- **Pid recycling.** The `pid` in the fence is `os.getpid()` of the submitter. A recycled pid can only match while
  the task row still names that exact run, claim and pid.

## Plugin?

No. This is core Kanban dispatcher and worker-client behavior.

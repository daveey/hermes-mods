# Closed-window blocked asks

## Behavior

A card-owned job that expires (`withdrawn`, reason `expired after …`), is abandoned
(dead runner or no window after launch grace), or fails to open must not leave a
blocked ask pointing at a window that is gone.

`watch_once` reconciles both new terminal outcomes and previously final jobs.
`sweep_jobs` also visits `final.lock` jobs, so an old watchdog or a failed board
write can be repaired on a later call. The job's final outcome is never changed
by board-reconciliation success or failure.

For a still-blocked card with no current run and no later-run attachment:

- Set only `human_request.action` to `window closed HH:MM PDT. Unblock = agent
  reopens window; archive = drop request.` The timezone is `America/Los_Angeles`,
  so winter uses PST. The final result file's mtime supplies a stable end time
  for old jobs as well as new ones.
- Preserve all other request fields, including decision choices. Read back the
  classified request inside the transaction and roll back if core normalization
  changes anything beyond the action.
- Add one audit comment per job with its ID, status, cause and ISO end time.
  The durable `[user-terminal:closed:<job-id>]` marker deduplicates later passes.
- Do nothing to ready/unblocked, done, archived, triage or currently running
  owners, jobs attached to a later run, or requests that ran, were declined or
  were explicitly withdrawn. Do not invent a missing human request.

Expiration, withdrawal, reattachment and process-kill policies are unchanged.
This is local-board reconciliation only; remote relayed jobs still do not carry
local board ownership (see [REMOTE.md](REMOTE.md)).

## Implementation and core prerequisite

`reconcile_closed_ask` holds the attachment lock and a core `write_txn` around
eligibility, marker lookup, `classify_parked_request`, readback and `add_comment`.
It never issues a raw board UPDATE. Core descendant fences remain enforced;
worker callers keep their environment/protections and leave reconciliation to
the existing detached watchdog or a later eligible sweep. Failures are logged
and leave the final job result unchanged.

**Requires a composable core classifier:**
`hermes_cli.kanban_human_request_migration.classify_parked_request` must use
`db.write_txn(conn, allow_nested=True)`. Older cores refuse the nested transaction;
the plugin logs the refusal and makes no partial board change. Deploy that core
prerequisite before claiming the reconciliation is operational.

The standalone watchdog lazily locates the parent's core checkout (carried in
`HERMES_USER_TERMINAL_CORE_ROOT` by both launch paths), or the installed checkout
for older jobs, and activates its committed dependencies before a fresh import.
It never deletes/replaces an active process's core modules, runs a package
installer, or relaunches the process to import core.

## Tests and evidence

`tests/test_closed_asks.py` uses real core APIs on a disposable database directly
under the user's home, outside fenced workspaces. It isolates `HERMES_KANBAN_HOME`,
checks the resolved path and fence before initialization, and preserves inherited
descendant markers. No live board, credential or GUI window is touched.

Coverage:

- Existing final expiry/abandonment/open-failure through both watch and sweep;
  repeated passes produce one comment, with all other task/request fields intact.
- Newly detected expiry, dead runner, missing window and failed opener.
- PDT and PST formatting, and stable end time across retries.
- Exact no-write snapshots for ineligible owner/job states and write-fence refusal.
- Atomic rollback on comment failure, followed by successful final-path retry.
- Fresh standalone `__init__.py --watchdog` import and loaded-module identity.

Baseline `0ef1c614`: the six watch/sweep positive cases and four new-outcome cases
fail (10 failures); all 12 no-write cases pass. The baseline fixture borrows only
the new lazy core loader to initialize its isolated test board; its original
watch/sweep implementation remains unchanged.

The pre-existing targeted suite (`tests_user_terminal.py`,
`tests/test_reattach_grace.py`, `tests/test_run_outcomes.py`, and
`tests/test_state_reads_read_only.py`) passes **204 tests** under the Hermes
Python 3.14 runtime. Its real PTY cases take about six minutes on macOS.
The new real-core suite passes **26 tests** against core commit `44e466c111`
(the composable classifier prerequisite). Its two new core transaction tests fail
before that fix, and all 44 metadata-entry core tests pass afterward. The plugin
suite was exercised on Linux, including the standalone watchdog subprocess;
existing PTY behavior was exercised on macOS. Deployment/live proof is separate.

## Five whys

Problem: a blocked card can retain an instruction to authenticate in an expired
or dead terminal window.

1. Why does the ask still point at the window? The baseline `watch_once` returns
   final expiry/abandonment/failure outcomes without any board propagation
   (`__init__.py` at `0ef1c614`, `watch_once`, `fail_job`).
2. Why does polling not repair it? The final-status branch immediately returns;
   `sweep_jobs` skips directories with `final.lock` entirely.
3. Why was no shared lifetime enforced? Board checks implement only card → job
   withdrawal. There was no job → blocked-ask metadata reconciliation step.
   This is the controllable design gap, not an expiry-policy problem.
4. Why was it not caught? Existing watchdog tests assert job status and process
   teardown; their minimal board fixtures do not carry `human_request` or audit
   comments (`tests_user_terminal.py`, `tests/test_reattach_grace.py`).
5. Why did those assertions suffice? Their contract covered prompt survival and
   reattachment, not the lifetime of the user-facing ask. A complete job-state
   test could therefore pass with a stale ask.

Countermeasures: reconcile new and old final outcomes; make classification plus
comment atomic/idempotent through core; prove no writes on ineligible states;
add real-core ask-lifetime and standalone-import regressions. Existing withdrawal
and reattachment tests remain unchanged. Live rollout/proof belongs to the
parent task; this document does not claim it has happened.

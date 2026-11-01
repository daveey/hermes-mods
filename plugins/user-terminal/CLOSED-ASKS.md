# Closed-window blocked asks

## Behavior

A card-owned job that expires (`withdrawn`, reason `expired after …`), is abandoned
(dead runner or no window after launch grace), or fails to open must not leave a
blocked ask pointing at a window that is gone.

`watch_once` reconciles both new terminal outcomes and previously final jobs.
`sweep_jobs` also visits `final.lock` jobs, so an old watchdog or a failed board
write can be repaired on a later call. The job's final outcome is never changed
by board-reconciliation success or failure.

For a still-blocked card with no current run and no later-run attachment, and
only when the card's current ask was set while this job's window was live:

- The binding: the latest `blocked` task event not written by this plugin (a
  block, or another actor's metadata-only edit) must fall between the job's
  `created` time and its end time, and carry the job's run id when both are
  known. A job that failed while the card was running, an ask set before the
  window opened or after it closed, an ask from another run, or a card with no
  block event at all gets no write: the window is not what the card waits on.
- Set only `human_request.action` to `window closed HH:MM PDT (it was for:
  <the job's reason, up to 80 chars>). Unblock = agent reopens window; archive =
  drop request.` The timezone is `America/Los_Angeles`,
  so winter uses PST. The final result file's mtime supplies a stable end time
  for old jobs as well as new ones.
- Preserve all other request fields, including decision choices. Validate the
  amended request with core's validator before any write and write nothing if
  core normalization would change anything beyond the action.
- Add one audit comment per job with its ID, status, cause and ISO end time.
  The durable `[user-terminal:closed:<job-id>]` marker deduplicates later passes.
- Do nothing to ready/unblocked, done, archived, triage or currently running
  owners, jobs attached to a later run, or requests that ran, were declined or
  were explicitly withdrawn. Do not invent a missing human request.

Expiration, withdrawal, reattachment and process-kill policies are unchanged.
This is local-board reconciliation only; remote relayed jobs still do not carry
local board ownership (see [REMOTE.md](REMOTE.md)).

## Implementation

`reconcile_closed_ask` holds the attachment lock (which serializes it with a
resumed run's publication and with every other pass over the same job) and
never calls a core writer inside a transaction it opened. Core's
`classify_parked_request` opens its own `write_txn`; cores before the
composable classifier (and stale editable core snapshots, see below) refuse to
nest it, so the plugin composes with core's API in three steps:

1. One plugin `write_txn` that calls no classifier: re-check eligibility
   (blocked, no current run, a `human_request` with an action, the same bound
   block event id), pre-validate that core normalization changes nothing but
   the action, and add the `[user-terminal:closed:<job-id>]` audit comment once
   (`add_comment` is a nesting composition primitive in every core).
2. `classify_parked_request` in core's own transaction, which itself re-checks
   blocked/no-run atomically with its write.
3. A check that no non-plugin `blocked` event landed between the bound event and
   the plugin's amendment. If one did (another agent re-blocked in the
   microseconds between 1 and 2), the newer ask is classified back
   (actor `user-terminal-restore`), so a closed window only ever leaves the ask
   it served rewritten.

Choice (decided spec option): the re-check runs immediately before core's write
in a plugin-owned transaction, not inside core's write, because core offers no
compare-and-swap hook into the classifier without `allow_nested`; step 3 closes
the gap that leaves. Dedup: the comment is written once per job (marker); the
amendment is skipped once the current action already equals the closed-window
action. A comment failure rolls step 1 back with nothing written; a classify
failure after the comment leaves the comment and a later pass amends the ask.
It never issues a raw board UPDATE. Core descendant fences remain enforced;
worker callers keep their environment/protections and leave reconciliation to
the detached watchdog or a later eligible sweep. Failures are logged and leave
the final job result unchanged.

**Bounded retry.** `reconcile_closed_ask` returns False only when an attempt
raised. The watchdog then retries every `RECONCILE_RETRY_INTERVAL_S` (60 s) for
up to `RECONCILE_RETRY_WINDOW_S` (10 min), logging each failure once with its
exception text, then logs `leaving it to the next sweep` and exits; after that
only `sweep_jobs` (run by the tool handler) retries. The watchdog waits up to
`WATCHDOG_BUSY_TIMEOUT_S` (30 s) for the board's write lock (the first live run
hit `database is locked` with a 2 s wait and recovered on the 60 s retry); the
tool handler's sweep keeps 2 s so a busy board never stalls a prompt.

**Interpreter.** `watchdog.sh` and `spawn_watchdog` both exec `watchdog_python()`,
the gateway's own `sys.executable` (on macOS the signed `Hermes Python.app`
twin of the launcher's runtime Python). The standalone-watchdog test resolves
that interpreter the same way (`--print-runtime-command`, then
`signed_python_app`), or `HERMES_USER_TERMINAL_WATCHDOG_PYTHON`, and skips with
the reason only when none exists.

Why the nested write failed on one 3.14 interpreter but not 3.11: it was the
import, not the Python version. Run as the install's managed env venv Python
(`installs/<id>/environments/<id>/venv/bin/python`), that venv's uv editable
finder (`__editable___hermes_agent_*_finder`) resolves `hermes_cli` to the
environment's build-time `workspace/` snapshot before the watchdog's core-root
fallback runs, and that snapshot predates the composable classifier (its
`classify_parked_request` uses a plain `write_txn`). The 3.11 core venv and the
production `Hermes Python.app` have no such finder, so they import the live
checkout, whose classifier opts into nesting. The three-step path works with
either core.

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
- Composition with a classifier that refuses nesting (simulated pre-composable
  core), the watchdog's own retry (classify fails once, then succeeds: one
  comment, one amendment, one logged failure), the retry giving up after its
  window, and a block landing between the re-check and core's classify (the
  newer ask is restored and later sweeps write nothing). On `3e8290a5` all four
  fail; the standalone watchdog test fails there when run as the managed env
  venv Python (nested `write_txn` RuntimeError) and passes on `Hermes Python.app`.
- Ask binding (review round 1): a job that failed while the card ran, then a
  later block on an unrelated ask by the same run, a later run, or another
  actor's metadata-only edit, gets no write on any later sweep; so does a card
  with no block event, a block before the window opened, or a block in the window
  by another run. A sweep after this plugin's own amendment reads past its event
  and still dedups. On `99ab1291` the six unrelated-ask/binding cases fail (they
  rewrite the ask); all 42 closed-ask + opener tests pass with the binding.

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

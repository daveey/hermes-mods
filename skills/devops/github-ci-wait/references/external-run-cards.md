# External-run cards: structured poll output

For a native single-run/PR wait, set `awaiting.kind` to `ci` (or `deploy` for a deploy)
and `awaiting.ref` to the actual GitHub run/PR URL. The native GitHub adapter supplies
service/job health, not review or landing approval. A green run is not permission to merge.
Default native polling is 300 seconds.

For an aggregate or containment predicate, verify the installed adapter selection before
parking: `kanban_external_watch.plan()` can select `github_run`/`github_pr_checks` ahead of a
supplied `wake_check` when the metadata names a native GitHub target. Use `awaiting.kind=external`
with a workflow-level producer reference when the shell predicate must govern readiness;
read back that the persisted adapter is `shell`, the exact command is retained, and its files
and credentials are available on the dispatcher's probe host. Run the exact command there.
A supplied `wake_check` alone does not prove the scheduler will execute it.

For an opaque shell/commit-containment probe, **each poll must print one unfiltered
`HERMES_WATCH {json}` line to stdout**, including pending and error paths, before exiting.
Emit it in the wake-check producer, not once in the park reason or agent chat. Use a JSON
serializer on the structured response already read by that producer; never interpolate raw
stderr or credentials into JSON. Contract (illustrative pending response, not live evidence):

```text
HERMES_WATCH {"job":"queued","service":"ok","service_name":"GitHub Actions","detail":"waiting for a runner","url":"https://github.com/OWNER/REPO/actions/runs/RUN_ID"}
```

- `job`: `queued|running|succeeded|failed|cancelled|unknown`, from the observed run/jobs.
- `service`: `ok|degraded|down|unreachable|auth_failed|rate_limited|probe_broken` is
  **service health**, not the display name; `service_name` is `GitHub Actions`.
- `detail`: concise observed status/error (no secrets); `url`: actual run/PR URL or null.
- `retry_after`: nonnegative integer seconds on rate limits. Do not call a 429 an outage.
- The dispatcher stamps `observed_at` and `next_due_at`; do not invent last/next checks.

A completed failed/cancelled job is `job=failed|cancelled, service=ok`: wake the worker to
handle that result; it is not service-down red. A timeout/5xx is `job=unknown,
service=unreachable`; authentication failure is `auth_failed`; a broken local probe is
`probe_broken`. Use only an error classification the actual response supports.

Keep wake semantics: exit 0 when finished (green **or red**), 1 pending, 3 unreadable/unknown.
A typed terminal job also wakes the card, even at exit 1 or 3. For an aggregate predicate,
`job` must describe the aggregate rather than one already-completed member: pending output
must be nonterminal (`running`/`queued` only when observed, otherwise `unknown`), and unreadable
output must use `unknown` with the evidence-supported service error. Pass pending, ready,
new-required-failure and unreadable predicate outputs through the installed
`kanban_external_adapters.shell.poll(rc, full_output)` and assert both parsed fields and
`terminal`; standalone exit-code tests miss this boundary. Unexpected exit codes override
the typed line as probe failure. Never pass `wait_for_ci.py --once` exit 2 straight through:
that means pending to the waiter but broken to the shell watcher. Its exit 1 conflates stalled
CI and read/auth failure, so it alone cannot justify `job=failed` or `service=ok`; inspect the
response before emitting a terminal result. Preserve the original verdict/exit in the wrapper
and do not hide it behind a filter pipeline. `wait_for_ci.py` itself does not park cards and
retains its existing CLI exit contract; the wake-check wrapper owns translation and structured
output.

## Whole-commit wait measurement

Audit widened waits per parking episode, ending at its first condition/clock wake or other wait-ending event; do not attribute a later park's result to the old widening. Split shell-probe wakes from native commit results, and distinguish explicit 60-minute `external_result.outcome=pending` caps from shorter `wake_at` clock backstops. Deduplicate dispatcher comments by next run before counting no-op reparks. Separate same-SHA workflow waits from PR-head-to-merge-group checks and train submission/landing progress. Report positive `wake_event.created_at - wake_at` latency explicitly: polling explains it but cannot satisfy literal zero-lateness acceptance. No failure observed during active widened episodes means that failure path was not exercised, not independently proven timely.

## Auditing which cards wait on CI

There is no CLI filter for this; read the live board read-only via
`~/.hermes/bin/hermes-core-python -m sqlite3 ~/.hermes/kanban.db` (default board; other boards are
`~/.hermes/kanban/boards/<slug>/kanban.db`; `~/.hermes/kanban/default.db` is an empty stub).
1. Take non-running cards only (`scheduled|blocked|todo|triage`). A `running` card with a live
   `current_run_id` has a worker doing its own wait; leave it.
2. The CURRENT wait is the `awaiting` in the newest `scheduled|blocked|continued|status` event.
   Older events and `external_watch` rows keep stale CI refs after a card moved on to a
   parent/clock/chat wait, so a "latest event mentioning awaiting" scan overcounts.
3. Check each ref with `gh run view` / `gh pr view --json statusCheckRollup`, not the card text.
4. A `triage` card whose awaiting is `ci` is usually a spend-budget hold that fired when CI
   woke it (`spend_budget_held` event). If CI is green and the remaining steps are bounded
   (review, ship, verify), re-scope in the reason and `hermes kanban promote <id> "<reason>"
   --seen <newest comment id>`, then confirm a `claimed`/`spawned` event follows. If a supervisor's
   newest comment deliberately RETAINS the hold, leave it.
5. Red CI on a card that already parked for its creator's disposition: check whether the
   creator answered (newer comment, card ready/running) before acting; the owner usually has it.

On resume, reconcile the real exact-head checks and review/landing gates. The blue external
state and its hover (service, status, last/next check, URL) are visibility, not acceptance.

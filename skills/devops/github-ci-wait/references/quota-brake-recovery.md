# Waiter exit 75 with `blocked=shared_quota_brake`

Exit 75 is a monitor outcome, not a CI verdict.

1. Ledger (`sqlite3 -readonly ~/.hermes/logs/github-quota/audit.sqlite`; if locked use `"file:...audit.sqlite?immutable=1"`): the waiter's `vends` row shows `blocked=shared_quota_brake`, `failure_phase=running`, `failure_class=Denied`; its `samples` have phase `running_shared_not_attributable`. Other hub processes drained the shared bucket; the waiter did not overspend, and restarting it only trips the brake again.
2. `select datetime(reset,'unixepoch'), remaining from quotas`: once `reset` has passed (remaining back near 5000), the brake no longer applies. Until then do NOT retry, loop, or route around it (no raw HTTP, no second token, no `gh` bypass, no "retry ship" round): immediate retries are other hub processes draining the same bucket.
3. After reset run ONE guarded check per call, each under its own broker exec: `token-broker exec --scope user/github --reason '<5+ chars>' -- python3 -B wait_for_ci.py --run-id ID --gh-repo OWNER/NAME --once`. Exact-run mode needs no local checkout.
4. Head binding is the caller's job: `--run-id` does not check which commit the run built. Read the run's `headSha` and the PR's current `headRefOid` (one `gh` read each), and treat any mismatch as "head moved, don't ship".
5. Exit 2 (pending): park the owner card with `kanban_schedule`: `reason` carries `SCHEDULED_UNTIL=<ISO8601, 10-30 min out>` plus the checkpoint (exact sha, run id, the `--once` command, "head moved => don't ship"); `awaiting` names the producer (the run id / owner card); `wake_check` or `clock_only=true` is required for a park over 2 h. `resume_after_seconds` is a `kanban_continue` argument, not a `kanban_schedule` one. Never start another long-lived waiter.
6. Exit 1: `gh run view ID -R OWNER/REPO --log-failed`, compare to main's red logs before naming a cause.

## Recurrence evidence (ledger `vends` rows, exit 75; brake column noted per row)

- PR811: `Durable exact-head PR811 b19 CI handoff to parent` at 2026-11-11 00:47:34 (session 20261111_004242_b11c3f). Earlier PR811 exact-head waits also ended 75 on 2026-11-04 08:41:47 (no brake column then).
- F5 PR1086: `land reviewed F5 PR1086 exact head ... via hermes-core-wt ship` exit 75 at 06:58:50, and `F5 PR1086 exact-head CI wait 17342dc` brake at 07:14:02 (session 20261111_070841_f45bad), on 2026-11-11. At the same time session 20261111_052948_28fcac logged `retry ship <slug> (quota brake) round 1` vends, each also `shared_quota_brake`, started minutes before and ending ~07:21.
- The F5 root cause and "why wasn't it caught" chain are not established by this evidence (no five-whys on record): treat them as open until the F5 owner card supplies them.

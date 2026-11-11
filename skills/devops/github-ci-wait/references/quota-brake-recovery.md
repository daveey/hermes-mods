# Waiter exit 75 with `blocked=shared_quota_brake`

Exit 75 is a monitor outcome, not a CI verdict.

1. Inspect the waiter's ledger rows using `sqlite3 -readonly ~/.hermes/logs/github-quota/audit.sqlite`: check `blocked`, `failure_phase`, `failure_class`, and associated samples before attributing exit 75. A shared brake can trigger while substantial quota remains; the denial alone does not establish which caller caused it.
2. Read `select datetime(reset,'unixepoch'), remaining from quotas` alongside the broker's current brake state. A healthy remaining count or elapsed quota reset alone does not prove admission is allowed: shared request-rate pressure also triggered the brake in repeated waits. While denied, do not restart the waiter or bypass the broker with raw HTTP, another token, or a ship retry loop.
3. Once the broker permits a check, run ONE guarded check per call, each under its own broker exec: `token-broker exec --scope user/github --reason '<5+ chars>' -- python3 -B wait_for_ci.py --run-id ID --gh-repo OWNER/NAME --once`. Exact-run mode needs no local checkout.
4. Head binding is the caller's job: `--run-id` does not check which commit the run built. Read the run's `headSha` and the PR's current `headRefOid` (one `gh` read each), and treat any mismatch as "head moved, don't ship".
5. Exit 2 (pending): park the owner card with `kanban_schedule`: `reason` carries `SCHEDULED_UNTIL=<ISO8601, 10-30 min out>` plus the checkpoint (exact sha, run id, the `--once` command, "head moved => don't ship"); `awaiting` names the producer (the run id / owner card); `wake_check` or `clock_only=true` is required for a park over 2 h. `resume_after_seconds` is a `kanban_continue` argument, not a `kanban_schedule` one. Never start another long-lived waiter.
6. Exit 1: `gh run view ID -R OWNER/REPO --log-failed`, compare to main's red logs before naming a cause.


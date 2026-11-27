# Crashed runs: "why are there failed tasks and why weren't they restarted?"

No scanner rule covers this; diagnose by hand.

1. Restarted? The dispatcher retries `crashed` runs on its own. Read `hermes kanban runs <id>` for a later run and
   `kanban_show` for live status/heartbeats. Chain rows in the panel saying `status unknown` are a stale
   projection: confirm each with `kanban_show` before calling them stuck.
2. Why it crashed: `managed_worker_failed` / `EOFError` / `request_failed` in the run error is only the symptom.
   Read the worker's gateway log at the run's `ended_at`:
   `~/.hermes/bin/hub-worker-exec <worker-aN> --timeout 90 -- bash -lc "grep -h '<YYYY-MM-DD HH:MM>' ~/.hermes/logs/gateway.log | grep -i 'lost\|error\|fail'"`
   (`--` before the command is required). Then `df -h /` on that worker.
   - `No space left on device` / `database or disk is full` → worker disk; size `~/.hermes/kanban/workspaces`,
     `~/.hermes/cache`, `/tmp`.
   - many cards crashing in the same minute on different workers → hub-side (e.g. gateway FD exhaustion).
3. A crash that took several cards at once is a host incident: search open cards for it first, then file one
   card to find the producer and prevent recurrence (not a cleanup sweeper).

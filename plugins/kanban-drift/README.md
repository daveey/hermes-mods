# kanban-drift

One owner-host scheduled consumer of fresh `hermes kanban sync diff --json` reports.
It does not mutate board databases, replay rejected writes, or use replicas as authoritative evidence.
Configure host-local JSON with `owner_gateway`, `state_path`, `targets` (gateway/board → command argv),
`assignee`, `board` and optional command `timeout` (seconds). Run `monitor.py --config <file>`
through the installed Hermes Python bootstrap so native Kanban tools and plugins are loaded.
Keep connection addresses and credentials out of this public repository.

Two consecutive dirty samples create one agent-owned repair card through `kanban_create`.
A persisted incident UUID makes create retries idempotent, including a crash after the tool's write.
Unchanged samples do not spam comments; changed evidence refreshes the existing card.
Clean resets the streak, but never closes the repair card. Unavailable resets the consecutive-sample
streak without clearing its incident. A done/archived card permits a new incident after two new dirty
samples. A state-file flock prevents concurrent consumers; file writes are atomic and fsynced.
Command errors, malformed output and non-boolean clean values are unavailable, never clean.
The consumer verifies its configured gateway before running any command or writing state.

Each target row records `unavailable_since` (first unavailable sample of the current dark period;
cleared by any real clean or dirty sample). Exit codes: `0` when every target was sampled or has been
unavailable for at most `unavailable_alert_after` seconds (default 21600 = 6 h; an int, or a map of
target key → seconds with optional `"*"` default), so a sleeping laptop does not fail the job; `1` when a
sample hit a `consumer_error` or a target has been dark longer than its threshold. The receipt lists those
targets in `stale_targets`, so the scheduler's failure detector catches a lasting blind spot.

Deployment is opt-in: install ONE cron/timer on an always-on gateway, recording owner, schedule ID,
config path, state path, command and receipt location. Disable that schedule to roll back; preserve
state for restart-safe dedup. Do not enable another consumer for the same targets.

## Tests

`scripts/test-all -k kanban-drift` exercises dirty-dirty, dirty-clean-dirty, restart, unavailable,
closed-card renewal, crash-after-create recovery, command failures, duplicate suppression, and
main() exit codes for short vs stale unavailability (global and per-target thresholds).
All card writes use isolated sinks (including a native-tool integration test with an explicitly
isolated canonical Kanban path); tests cannot post live repair alerts. Malformed command output
retains the exit code and bounded stderr diagnostic, while still failing unavailable.

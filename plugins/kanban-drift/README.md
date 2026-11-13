# kanban-drift

One owner-host scheduled consumer of fresh `hermes kanban sync diff --json` reports.
It does not mutate board databases, replay rejected writes, or use replicas as authoritative evidence.
Configure host-local JSON with `owner_gateway`, `state_path`, `targets` (gateway/board → command argv),
`assignee`, `board` and optional command `timeout` (seconds). Run `monitor.py --config <file>`
through the installed Hermes Python bootstrap so native Kanban tools and plugins are loaded.
Keep connection addresses and credentials out of this public repository.

Two consecutive dirty samples create one agent-owned repair card through `kanban_create`, with
`placement: "auto"` so it joins the worker pool: on the hub, `"local"` is a hub slot that the admission
judge must admit, and it refuses routine repair work (pool workers have hub ssh and hub-admin).
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

Elapsed time alone cannot tell "host is off" (a closed laptop, a trip) from "host is up but sampling it
fails". Optional `presence` (map of target key → argv, run only while that target is unavailable,
`presence_timeout` seconds, default 15) answers it: exit 0 = host up, any other exit = host down. A host
that is down is listed in `absent_targets` and fails the run only after `absent_alert_after` (default
604800 = 7 days; int or per-target map like above). A host that is up while its sample keeps failing still
fails after `unavailable_alert_after`. A probe that cannot run or times out counts as up, so a broken probe
never hides a real failure. `stale_reasons` maps each stale target to `unavailable_too_long` (no presence
configured), `sample_failing_while_up` or `absent_too_long`. Keep presence argv (hostnames) in host config.

Deployment is opt-in: install ONE cron/timer on an always-on gateway, recording owner, schedule ID,
config path, state path, command and receipt location. Disable that schedule to roll back; preserve
state for restart-safe dedup. Do not enable another consumer for the same targets.

## Tests

`scripts/test-all -k kanban-drift` exercises dirty-dirty, dirty-clean-dirty, restart, unavailable,
closed-card renewal, crash-after-create recovery, command failures, duplicate suppression, and
main() exit codes for short vs stale unavailability (global and per-target thresholds), and presence:
an absent host for 14 h exits 0, a reachable host with failing samples exits 1, absence past 7 days
exits 1, and an erroring or hung presence probe counts as up. A fake create that follows core's hub
rule (a hub-slot pin without `hub_reason` is refused) must still file exactly one card, and a core-backed
guard runs the monitor's real create args through core's placement planner as the hub with a deny-all
judge: they must reach the pool without ever consulting the judge.
All card writes use isolated sinks (including a native-tool integration test with an explicitly
isolated canonical Kanban path); tests cannot post live repair alerts. Malformed command output
retains the exit code and bounded stderr diagnostic, while still failing unavailable.

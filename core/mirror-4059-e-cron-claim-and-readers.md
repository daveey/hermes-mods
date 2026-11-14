# Cron execution-reader and restart-safe claim liveness

Mirror ordinals: 3962, 3963.

## Behavior

A restart-safe external runner keeps its fire claim live after the claiming gateway exits. Additional tests pin populated execution-reader memo behavior.

## How it works

Claim heartbeats stamp runner in addition to refreshing at, retaining by as the claiming identity. Early death-based reclaim requires the claiming process to be provably dead and, when present, the runner also provably dead. Foreign or unprobeable identities fail safe. Execution-reader tests separately exercise cold/warm selection, fork isolation and shm-only invalidation.

## Code

- `cron/jobs.py`
- `cron/scheduler_ownership.py`

## Tests

- `tests/cron/test_claim_job_for_fire.py`: A real exited gateway PID plus this process as heartbeating runner prevents reclaim; replacing runner with another proven-dead process permits immediate reclaim.
- `tests/cron/test_list_jobs_executions_memo.py`: Newest of multiple live attempts wins cold and warm; excluded jobs with populated latest/live rows stay filtered. A forked child never queries its inherited reader, and shm identity change closes/reopens the reader while preserving answers and stable subsequent reuse.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

This preserves death-based TTL shortening semantics; an unproven process death cannot shorten the claim. Fork reader coverage requires a fork-capable environment.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

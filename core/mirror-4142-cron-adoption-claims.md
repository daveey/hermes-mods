# Cron adopted-claim stamping and loss booking

Mirror snapshot 4142; ordinals 4066, 4067.

## Behavior

- A detached cron worker that adopts an execution refreshes the owner-fenced fire claim before it writes the ready acknowledgement, so the claiming gateway can exit right after seeing the ack without leaving a live worker's job reclaimable.
- If the claim has been replaced or the job removed by then, the worker books the execution as lost before acknowledging, acknowledges the handoff, and returns success without running the job. No dispatch-failure notice is triggered by this path.

## How it works

- 4066: in `_run_external_worker_payload` (`cron/scheduler.py`), after the execution is adopted and before the ack is published, the worker reads the claim owner from the job and calls `heartbeat_fire_claim` with that expected owner. In 4066 a failed heartbeat logged an error and returned False.
- 4067: a failed heartbeat now sets `claim_lost` and calls `finish_lost_execution` with "Fire claim ownership lost before worker readiness". The ack is still published; if publishing fails the function still returns False. After a successful ack with `claim_lost`, it returns True and does not set the external-worker environment or call `run_one_job`.

## Code

Source paths touched by this feature group (including regression fixtures):

- `cron/scheduler.py`
- `tests/cron/test_external_worker_fire_claim_adoption.py`

Mail entries in the ordered series:

- 4066: [`1418b2046c51d105e3b7bbbbbf9f395641a1a77b`](patches/4066-fix-cron-stamp-adopting-worker-before-ready-acknowle.patch)
- 4067: [`62343a331c4decef0b7183a9cc783e6e940e23a6`](patches/4067-fix-cron-book-lost-adopted-claims-before-acknowledgi.patch)

## Tests

`tests/cron/test_external_worker_fire_claim_adoption.py` (new in 4066, rewritten in 4067):

- `test_worker_stamps_claim_before_ready_without_run_heartbeat`, parametrized on `claim_state` = `owned`, `replaced`, `removed` (two cases `False`/`True` in 4066). With the dead gateway's claim, the worker is the live adopter; `run_one_job` is stubbed so no run heartbeat can rescue the claim. For `owned`, at ack time the claim carries this worker's runner id and cannot be claimed again, the ack is written, the job runs, and the execution is `running`. For `replaced` and `removed`, the ack is written, the job does not run, the function returns True, the execution is `failed` and `cancelled` respectively, and a replacement claim is left untouched.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. The worker path is covered only through a direct call to the payload function; the spawn path by the gateway is untested here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Only the ownership check before readiness changed; later heartbeats in `run_one_job` are unchanged.
- A job with no recorded claim owner skips the stamp.
- If the ack cannot be written, the function still returns False and the dispatch-failure handling applies as before.
- This document makes no deployment claim.

## Plugin?

No. This is the core cron worker handoff and claim ownership contract, which a plugin cannot replace.

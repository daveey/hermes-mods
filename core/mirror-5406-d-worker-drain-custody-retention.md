# Worker drain: custody for background and housekeeping work

Mirror snapshot 5406; ordinals 5353, 5354, 5355, 5356, 5357, 5358, 5360.

Entries:

- **5353**: Retain background review drain lease through thread cleanup
- **5354**: Retain queued reviews and async housekeeping drain custody
- **5355**: Keep housekeeping custody implementation outside gateway facade
- **5356**: Retain cron cleanup drain custody beyond caller timeout
- **5357**: Retain independent drain custody for shared ingress tasks
- **5358**: Retain physical drain leases through executor and thread exit
- **5360**: Update idle review custody consumer for dispatch-owned settlement

## Behavior

- **5353**: background review lease retained through thread cleanup.
- **5354/5355**: queued reviews and async housekeeping own independent custody (new `gateway/run_drain_work.py`, kept outside the gateway facade).
- **5356**: cron cleanup custody persists beyond the caller's timeout until the real thread unwinds.
- **5357/5358**: shared ingress tasks hold independent custody, and physical leases last through executor and thread exit.
- **5360**: test consumer updated for dispatch-owned settlement.

## How it works

- `run_agent.py`, `agent/review_idle_queue.py`, `gateway/run.py`, `gateway/run_drain_work.py`, `cron/cleanup_timeout.py`, `cron/scheduler.py`, `hermes_cli/local_work_handle.py`, `hermes_cli/local_work_threads.py`.

## Code

Shipped non-test paths (union over these entries):

- `run_agent.py`
- `agent/review_idle_queue.py`
- `gateway/run.py`
- `gateway/run_drain_work.py`
- `cron/cleanup_timeout.py`
- `cron/scheduler.py`
- `hermes_cli/local_work_handle.py`
- `hermes_cli/local_work_threads.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_worker_drain_background_review.py`
- `tests/agent/test_review_queue_drain.py`
- `tests/hermes_cli/test_worker_drain_housekeeping.py`
- `tests/hermes_cli/test_worker_drain_cleanup.py`
- `tests/hermes_cli/test_worker_drain_cron_degrade.py`
- `tests/hermes_cli/test_worker_drain_local_work.py`
- `tests/hermes_cli/test_worker_drain_pool.py`
- `tests/agent/test_local_work_agent_seams.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Custody now outlives the caller; a hung thread keeps the drain open.
- Follows the baseline entries; apply only in order.

## Why core instead of a plugin

Thread/executor lifetime is owned by core runtime code.

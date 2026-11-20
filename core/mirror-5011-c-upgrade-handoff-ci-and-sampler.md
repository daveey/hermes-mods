# Upgrade CI release tags, busy handoff, and owner-receipt sampling

Patches: **4585–4586** (5011 snapshot).

## Behavior

Upgrade CI fetches both stable upstream tag eras even when a calver tag already resolves, so generation-specific suites can use the newer by-path updater. Origin canary tags are not stable starting points.

Handoff scenarios no longer wait for an update drain that idle-only pre-swap correctly refuses: they assert refusal, finish the worker, then retry idle. Long drain coverage uses an explicit real-socket drain with a 300 s fixture cap.

Process samples count owner receipt clients and legacy workers without descendant duplicates. Current workers export task identity in the environment, not chat argv. Probes carry task identity, not secrets.

## How it works

Workflow tag routing and fast-runner tests cover dual-era fetch. Scenario helpers separate busy-update refusal from `test_kanban_long_drain`. The namespace agent sampler retains legacy argv plus duplicate-root detection.

## Code

- `.github/workflows/tests.yml`
- `tests/e2e/core/upgrade/handoff/_handoff.py`
- `tests/e2e/core/upgrade/handoff/_scenario.py`
- `tests/e2e/core/upgrade/handoff/_ns_agent.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/ci/test_fast_runner_routing.py`
- `tests/e2e/core/upgrade/handoff/test_kanban_long_drain.py`
- `tests/e2e/core/upgrade/handoff/test_restart_wait_verdict.py`
- `tests/e2e/core/upgrade/handoff/test_worker_sampler.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

A later wait neither closes nor names holders of an earlier one. Full namespace validation remains a dispatch-CI concern. Shared-worker userns policy is untouched.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

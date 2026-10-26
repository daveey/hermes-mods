# Advertise the one-shot delegation budget

Patch: **0356**, source `d906269336ccb62b554afad513fb316d2deb3176`.

## Behavior

For finite one-shot runs, the dynamic `delegate_task` tasks description states the configured `delegation.oneshot_max_children` total across all calls, separately from the concurrent-child limit. It recommends completing remaining work inline. Interactive sessions and unlimited (zero) budgets do not receive this total-limit description.

An oversized batch with capacity remaining reports requested and remaining counts rather than incorrectly claiming exhaustion. The existing cap still applies; rejected batches consume no capacity.

## How it works

`_build_tasks_param_description` adds the configured total only for finite single-query sessions. It does not insert changing spent counts or mutate the static schema. `_oneshot_spawn_budget` distinguishes an oversized request from an exhausted budget. No budget reset or automatic re-decomposition is introduced.

## Code

- `tools/delegate_tool.py`: schema construction and budget refusal wording.
- `tests/agent/test_oneshot_footprint.py`: finite-cap descriptions, interactive/unlimited controls, charging and oversized-batch refusal.

## Tests

Against the complete applied public series in a standalone Linux verification clone:

```sh
scripts/run_tests.sh -j 1 -p hermes_bootstrap tests/agent/test_oneshot_footprint.py
```

Result: **8 passed, 0 failed**. Bootstrap is preloaded before the real-home isolation fixtures; no isolation guard was disabled. These are schema/helper contract tests, not live provider calls or Desktop render tests. Live deployment is untested by this mirror.

## Applying

Apply the complete ordered series, not this patch alone. All **283 patches** applied with `git am -3 --empty=keep` at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, after fresh upstream and fork fetches, through fork `d906269336ccb62b554afad513fb316d2deb3176`. All 282 previous patch files are unchanged, including privacy scrubs and the merge-adapted patch 0251.

## Gotchas / plugin alternative

Concurrency is not the total one-shot budget. The schema advertises the configured cap, not remaining per-call capacity, preserving stable prompt text. The authoritative schema builder and charging logic are core seams; a plugin monkeypatch would duplicate that contract. This does not change Kanban continuation limits or prove continuation-exhaustion routing.

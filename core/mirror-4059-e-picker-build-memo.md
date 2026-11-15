# Per-build provider-pool and auth-store memo

Mirror ordinals: 3966, 3967.

## Behavior

Shares repeated provider-pool reads and read-only auth-store parses within one picker build, never across builds.

## How it works

A ContextVar holds the normalized provider-key memo while list_authenticated_providers runs; nested scopes reuse it and finally restores the context. Auth parsing is keyed by path and file-cache version, so a persisted mid-build change is reread. Pool load errors are not cached. If version stat raises OSError, the ordinary auth loader fallback runs without memoization.

## Code

- `hermes_cli/model_switch_providers.py`

## Tests

- `tests/hermes_cli/test_picker_pool_memo.py`: Bounds duplicated pool loads through the model-options build (allowing the two outside-picker current-provider reads), checks normalized nested scope reuse and fresh outside reads, next-build exhaustion, cooldown picker visibility and a mid-build auth write. A synthetic replay test asserts its defined CPU budget.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

The auth-store result is read-only. Parallel prefetch threads without this context continue to load afresh. The stat-failure follow-up adds no dedicated regression test in these patches; the replay fixture is not a live performance acceptance result.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

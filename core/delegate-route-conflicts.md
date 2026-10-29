# Reject certain delegated model/provider conflicts

Patch: **0356**, source `78c05251a087b61b3f95b863c76f37b285a2bfd7`. Extends [model tiers and per-task routing](model-tiers.md).

## Behavior

`delegate_task` rejects a batch before constructing any child when a newly selected model has a recognized vendor incompatible with its resolved stock single-vendor provider endpoint. The error names the zero-based task index, model and provider, and asks for a compatible model or an explicit matching provider. A mixed batch is rejected atomically; no child runs first.

Unknown model vendors, custom endpoints, aggregators and purely inherited routes remain permissive. Providers are never switched automatically and there is no paid fallback. This is compatibility validation, not an availability, entitlement or rate-limit check.

## How it works

The existing resolved-route preflight invokes `task_route_conflict` after credential resolution and before child construction. It inspects effective inherited provider/endpoint fields without mutating the parent, configuration or task arguments. Only recognized model vendors on stock endpoints are eligible for rejection. No model-catalog network probe is used.

## Code

- `tools/delegate_tool.py`: batch route preflight.
- `tools/delegate_tool_routes.py`: conservative offline compatibility predicate.
- `tests/tools/test_delegate_route_conflict.py`: registered-tool-handler tests.

## Tests

Against the complete applied public series in a standalone verification clone on Linux:

```sh
scripts/run_tests.sh -j 1 -p hermes_bootstrap tests/tools/test_delegate_route_conflict.py
```

Result: **23 passed, 0 failed**. Cases cover atomic mixed-batch rejection, compatible explicit routing, unknown models, custom and aggregator endpoints, configured and task-specific mismatches, immutable parent/config/task inputs and absence of catalog probes. Child agents and credentials are synthetic; no live provider request or deployed UI behavior is claimed.

The initial run without early bootstrap import had 23 setup errors: lazy bootstrap read the workspace parent manifest after the real-home I/O guard activated. Loading `hermes_bootstrap` before fixtures avoids that harness-order issue without disabling the guard or modifying runtime code.

## Applying

Apply the full ordered series, not this patch alone. All **282 patches** applied successfully with `git am -3 --empty=keep` at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, using upstream `765933299d885e850390ed1d8e4fd4d37fae8342` and fork `78c05251a087b61b3f95b863c76f37b285a2bfd7`. Existing privacy scrubs and the merge-adapted patch 0251 are retained.

## Gotchas / plugin alternative

A model-only pin inherits the effective provider; specify both when changing vendors. Unknown/custom routes are intentionally not validated exhaustively. This does not fix provider rate limits. The resolved parent route is available at this core preflight seam, not to an ordinary parameter-only plugin hook. No new plugin or setting is needed.

# TUI RPC method lanes, projects tree caching and GC budget

Patches: 6074, 6075, 6076, 6077, 6078, 6079, 6113, 6114, 6115, 6116.

## Behavior

- Each long RPC method is capped at half the worker pool (always leaving one worker free); excess calls wait FIFO without holding a worker. HERMES_TUI_RPC_METHOD_CAP=0 disables the cap.
- Hand-offs after cancellation run off the cancelling thread and release waiting calls if no thread can start.
- Projects tree builds skip warm roots, memoise the non-workspace root policy for 30 s, share identical overlapping builds (a caller only receives a build that started after it asked), and cache canonical paths for 30 s (stored roots resolve uncached).
- Gateway and serve processes raise the young GC threshold to 20,000 by default (HERMES_GATEWAY_GC_THRESHOLD0 overrides it) and time collections for loop accounting.

## How it works

rpc_method_lanes, coalesced_build, git_probe, methods_projects, gc_budget and loop_accounting.

## Code

Paths touched by these entries (non-test):

- `gateway/gc_budget.py`
- `gateway/run_bootstrap.py`
- `hermes_cli/loop_accounting.py`
- `hermes_cli/web_server.py`
- `tui_gateway/coalesced_build.py`
- `tui_gateway/git_probe.py`
- `tui_gateway/methods_config.py`
- `tui_gateway/methods_projects.py`
- `tui_gateway/rpc_dispatch.py`
- `tui_gateway/rpc_method_lanes.py`
- `tui_gateway/server.py`

## Tests

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/gateway/test_gc_budget.py`
- `tests/tui_gateway/test_coalesced_build.py`
- `tests/tui_gateway/test_git_probe_warm_settled.py`
- `tests/tui_gateway/test_non_workspace_policy_cache.py`
- `tests/tui_gateway/test_projects_tree_coalesced.py`
- `tests/tui_gateway/test_rpc_method_lanes.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Admission reservations are released exactly once, including cancelled calls.
- Stored policy roots resolve uncached.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the TUI gateway server and gateway bootstrap inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.

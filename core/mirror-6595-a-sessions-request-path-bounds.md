# Sessions request path: transport packing, scoped enrichment and coalesced polls

Patches: 6031, 6062, 6091, 6126, 6127.

## Behavior

- A bounded ranked page and exact rank-ID reads cross SQLite as one typed aggregate row per result, avoiding per-row/per-column handoffs. Pinned hydration uses the same typed transport in 64-row JSON chunks: its handoffs scale with the number of chunks, not independently of the pin count.
- Persisted subagent reads are scoped to the requested page's profiles and lineage IDs using the existing parent index, with a small LRU keyed by the parent set; single-profile lists use the batched display annotation.
- Paint and edge-report diagnostics run on a bounded lane with two workers and return 503 with Retry-After when full, instead of sharing the default executor.
- Display polls for session detail and kanban task lookups coalesce identical in-flight requests; remote-link refresh starts are spaced per scope with bounded queue and cooldown history. The kanban task lookup specifically sends coalescing overflow straight to its lane runner so lane queue deadlines are preserved; session detail does not opt into that overflow mode.

## How it works

hermes_state_page_pack and hermes_state_rank_memo build the typed rows; web_routers.sessions routes through coalesced_read and run_in_lane; kanban_links_refresh enforces the start floor with a monotonic clock injected for tests.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_links_refresh.py`
- `hermes_cli/subagent_session_activity.py`
- `hermes_cli/web_read_coalescing.py`
- `hermes_cli/web_routers/sessions.py`
- `hermes_cli/web_thread_lanes.py`
- `hermes_state_page_pack.py`
- `hermes_state_rank_memo.py`
- `hermes_state_recency.py`
- `hermes_state_sessions.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_kanban_links_refresh_coalesce.py`
- `tests/hermes_cli/test_kanban_links_stale_scopes.py`
- `tests/hermes_cli/test_session_detail_kanban.py`
- `tests/hermes_cli/test_session_route_coalesce.py`
- `tests/hermes_cli/test_session_route_deadline.py`
- `tests/hermes_cli/test_sessions_display_annotation.py`
- `tests/hermes_cli/test_stripe_report_http_starvation.py`
- `tests/hermes_cli/test_subagent_page_scope.py`
- `tests/hermes_cli/test_web_links_stale.py`
- `tests/hermes_state/test_pinned_transport.py`
- `tests/hermes_state/test_rank_projection_transport.py`
- `tests/hermes_state/test_recency_hot_rows.py`
- `tests/hermes_state/test_request_rank_transport.py`
- `tests/hermes_state/test_session_filter_clause_order.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Filtering, ordering, snapshot and ranking authority are intentionally unchanged.
- Missing stores remain 'unknown', not empty.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the session routes and state-store read path inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.

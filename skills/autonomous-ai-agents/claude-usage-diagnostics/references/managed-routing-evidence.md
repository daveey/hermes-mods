# Managed worker routing evidence

## Independent reviewer admission

When repeated starts fail with `No eligible deep review model distinct from [...]`, diagnose on the admitting worker before promoting again:

1. Preserve the implementer exclusion set. Enumerate `kanban_review_model.independent_routes(excluded, kanban_cfg, assignee)` under its effective config; compare `kanban_provider_walls.active_walls()`, `kanban_llm_pacing_dispatch.held_pools(...)`, and each candidate's `live_quota_wall(provider, model)`. A provider-wide wall can exclude a different model whose model-specific quota check is clear.
2. Probe the independent candidate with that worker's own credentials using `scripts/probe_anthropic_pool.py <model>`. A clear quota check alone is not completion evidence. Preserve genuinely exhausted accounts and unrelated pacing holds.
3. If live completions disprove the provider-wide wall, use the scoped operator recovery from SKILL.md §3e. Where installed, prefer `kanban_provider_walls.drop_wall(provider, not_after=<observed-wall-deadline>)` over rewriting the walls file: it preserves a later wall recorded since inspection. This is recovery of the disproved hold, not a waiver of reviewer independence or proof the wall's producer is fixed.
4. Comment the evidence on the existing review card and promote on its home gateway with `--seen <latest-read-comment-id>`. Read back there: a synced replica can still show triage after promotion. Verify a new normal-dispatch run and worker PID before claiming pickup; use the result evidence below before claiming review completion.

## Allocation and completion evidence

Read effective `kanban_llm_pacing_dispatch.load_config().pace_starts` before expecting a PACED pool to block or reroute starts: with paced-start limits disabled, PACED is not HELD. Preserve the approved governor policy; never manufacture a wall to satisfy a live proof.

Read actual class allocations from `model-routing.sqlite3.routes.metadata` (`selected_provider`, `selected_model`, `billing_group`, `exact_pin`, `pin_source`), then correlate the root/session with the Kanban run. A `run_route` event can still contain `class:deep` and provider null before allocation; concrete route events use `provider`, whereas `worker_result` uses `route_provider`.

A scratch completion, auxiliary allocation or subagent session is not a completed Kanban card: require the actual run result and card disposition. Join `task_runs.metadata.worker_session_id` to `sessions.id` and `routes.root`; require the same run's `worker_result` provider/model and clean exit plus completed run/card. A null `sessions.ended_at` alone does not override those terminal worker receipts.

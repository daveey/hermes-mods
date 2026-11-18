# Managed worker routing evidence

Read effective `kanban_llm_pacing_dispatch.load_config().pace_starts` before expecting a PACED pool to block or reroute starts: with paced-start limits disabled, PACED is not HELD. Preserve the approved governor policy; never manufacture a wall to satisfy a live proof.

Read actual class allocations from `model-routing.sqlite3.routes.metadata` (`selected_provider`, `selected_model`, `billing_group`, `exact_pin`, `pin_source`), then correlate the root/session with the Kanban run. A `run_route` event can still contain `class:deep` and provider null before allocation; concrete route events use `provider`, whereas `worker_result` uses `route_provider`.

A scratch completion, auxiliary allocation or subagent session is not a completed Kanban card: require the actual run result and card disposition. Join `task_runs.metadata.worker_session_id` to `sessions.id` and `routes.root`; require the same run's `worker_result` provider/model and clean exit plus completed run/card. A null `sessions.ended_at` alone does not override those terminal worker receipts.

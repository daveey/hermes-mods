# Codex: toolless turns end on commentary; class routes fail over at once on an exhausted pool

Patch: **1643** (`df6aa49e0c`, `codex: toolless turns end on commentary; class routes fail over at once on an exhausted pool`).

Related: [codex-subscription-routing.md](codex-subscription-routing.md), [subscription-quota-deadlines.md](subscription-quota-deadlines.md).

## Behavior

- **Commentary is the answer when no tools are offered.** A Codex (Responses API) model can end a completed response on
  a `phase=commentary` message. The normalizer marks that `incomplete` (commentary is the preamble to a tool call), so
  the loop re-called the model. For an agent that offers no tools (for example an API-server profile with tools off)
  there is no tool call to wait for, so 1-3 extra calls (+4-8 s) followed, and some turns walked the reasoning-only
  ladder into a class failover. Now such a response becomes the final text: the commentary is promoted to `content`,
  removed from `reasoning`, and `finish_reason` becomes `stop`, logged once at INFO.
- Promotion requires all of: no tools offered, no tool calls, no visible content, response `status == completed`, and
  every message item a completed `commentary` item with some `output_text`. Tool-using agents, partial/in-progress
  items, a final-answer item, or reasoning-only responses keep the existing continuation.
- **Continuations are logged.** Every Codex continuation logs `Codex continuation n/3: reason=commentary_only|reasoning_only|partial_text|empty`,
  the response status, `tools_offered` and the reasoning-only streak (never message text).
- **Eager class failover.** On a rate limit with no recoverable credential, an agent routed by model class (a
  `model_routing` dict that is not an exact pin, and no legacy fallback chain) now takes the eager failover immediately
  instead of a backoff sleep and a doomed retry (~4 s). Exact pins and unrouted agents keep the legacy gate.

## How it works

`agent/turn_response_intake.promote_toolless_codex_commentary(agent, assistant_message, response)` runs in the intake
when `api_mode == "codex_responses"` and the finish reason is `incomplete`, before hooks and guards.
`agent/turn_truncation._codex_continuation_reason` labels continuations. `agent/turn_recovery._class_failover_available`
widens the eager-failover condition to `is_rate_limited and class route`.

## Code

`agent/turn_response_intake.py`, `agent/turn_truncation.py`, `agent/turn_recovery.py`.

## Tests

`tests/agent/test_toolless_codex_turn_latency.py` (not rerun by this mirror pass):
`test_commentary_only_completed_response_is_incomplete_before_promotion`, `test_toolless_agent_promotes_commentary_to_final_answer`,
`test_other_incomplete_responses_keep_continuing`, `test_reasoning_only_response_is_not_promoted`,
`test_commentary_plus_final_answer_is_already_final`, `test_continuation_logs_its_reason_without_text`,
`test_exhausted_pool_on_class_route_fails_over_without_backoff`, `test_unrouted_or_pinned_agent_keeps_legacy_gate`,
`test_turn_intake_ends_toolless_commentary_turn_without_continuing`.

Untested: a live Codex endpoint (responses are constructed objects), and the end-to-end latency figures quoted above,
which come from the commit's production log sample.

## Applying

Apply in series order. No config.

## Gotchas

- A toolless agent that genuinely expected the model to keep going after commentary now stops at the commentary.
- The eager failover only fires when a class failover target exists; otherwise the normal backoff applies.

## Plugin?

No. Response normalization and recovery sit inside the agent turn loop.

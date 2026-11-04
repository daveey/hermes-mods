# API server: per-request context usage beside billing totals

Patches: **1760** (`0d0ee3a326`, `fix(api): report per-provider-request context usage beside turn billing totals [daveey]`); **1761** (`66b2f4f449`, `test(api): compare billing usage beside additive context_usage [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Chat Completions responses add optional `usage.context_usage = {version: 1, complete, requests: [{provider, model, input_tokens, input_limit}]}`, one entry per main-loop provider response; `usage.prompt_tokens` stays the turn's billing sum (1760/1761).

## How it works

`agent/context_usage.py` captures each response once before MoA fold; `gateway/platforms/api_server_usage.py` emits it.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1760: Chat Completions usage.prompt_tokens is the sum of every provider call in a gateway turn (billing). Consumers (BB-1207) read it as one request's context size, so a two-call turn looked like a 2x context peak.
- 1761: (subject only)

## Code

`agent/chat_completion_helpers.py`, `agent/codex_runtime.py`, `agent/context_usage.py`, `agent/conversation_loop.py`, `agent/turn_response_check.py`, `agent/turn_usage.py`, `gateway/platforms/api_server.py`, `gateway/platforms/api_server_usage.py`, `gateway/run_turn_runner.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_turn_context_usage.py`: `test_capture_is_a_noop_outside_a_turn`, `test_class_selector_identity_is_never_reported`, `test_codex_app_server_turn_is_adapter_aggregated`, `test_concurrent_agents_and_forks_are_isolated`, `test_export_tolerates_foreign_state`, `test_failed_attempt_then_retry_counts_only_the_received_response`, `test_fallback_switch_reports_the_runtime_that_answered`, `test_gemini_multi_hop_records_first_hop_and_marks_incomplete`, `test_gemini_single_hop_stays_complete`, `test_guessed_windows_are_null`, `test_hosted_tool_response_then_ordinary_call_lists_only_the_ordinary_one`, `test_identity_at_the_80_char_bound_is_kept` (+16 more)
- `tests/gateway/test_api_authority_daemon.py`
- `tests/gateway/test_api_context_usage.py`: `test_canonical_agent_without_context_usage_omits_it`, `test_canonical_response_without_usage_is_reported_incomplete`, `test_canonical_stream_and_nonstream_match_the_direct_path`, `test_canonical_two_measurements_keep_the_aggregate_sum`, `test_direct_concurrent_turns_are_isolated`, `test_direct_fallback_switch_reports_the_runtime_that_answered`, `test_direct_mid_turn_identity_change_is_per_response`, `test_direct_response_without_usage_is_reported_incomplete_not_faked`, `test_direct_retry_lists_only_the_received_response`, `test_direct_single_response`, `test_direct_stream_and_nonstream_carry_identical_context_usage`, `test_direct_two_measurements_keep_the_aggregate_sum` (+2 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

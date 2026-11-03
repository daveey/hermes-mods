# Compatibility audit: core PR 782

Candidate: `cfbf1f42a804af99fcfceecaac5c833d88c51cfa` (kanban capsule stack:
admission, owner binding, /workspace worker contract). Base: fork main
`4dfb4cc1f02a3600eed9bf0541f584e36fb8a254`, whose digests for the four files
below were already pinned (last entry of each list).

## Drifted files (main -> candidate)

- `agent/client_lifecycle.py`: the Anthropic SDK call passes
  `prefer_stream=False` while `observation_seam.active()`; the client is still
  `client or self._anthropic_client` (the request client).
- `agent/chat_completion_helpers.py`: `_dispatch_nonstreaming_api_request` gets
  `@observation_seam.sdk_request`, `handle_max_iterations` gets
  `@observation_seam.unsupported_summary`; `_assistant_reasoning_text` moves to
  `agent/chat_completion_helpers_reasoning.py` unchanged; blank-line removals.
- `agent/turn_api_call.py`: `_should_stream` returns False while an observer is
  active. `perform_api_call` is byte-identical.
- `agent/codex_runtime.py`: the Codex SSE open goes through
  `observation_seam.open_codex_stream(agent, active_client, kwargs)`, which is
  `active_client.responses.create(**kwargs)` unless `observer_runtime` is loaded.

## Seam review

- Client factories (`_create_request_anthropic_client`,
  `_create_request_openai_client`), `recover_with_credential_pool`,
  `_failed_credential_identity`, `switch_model`, both `interruptible_*_api_call`
  functions and `perform_api_call` are unchanged. None of them is decorated, so
  the `__wrapped__` refusal and code-object verification still hold.
- Every observation hook is a no-op unless the trusted capsule entry imported
  `observer_runtime` before Hermes. Ordinary workers, CLI and gateway never
  have it. Inside a capsule the hooks still receive the request-local client
  (`make_client` / `active_client`) that our wrapped factories built, so the
  selected credential is what gets sent. Forcing non-streaming only picks which
  `interruptible_*` path runs; both are wrapped by the terminal wrapper.
- No new credential or route fence that a request-local snapshot could bypass.
  `selection.py` needs no change, so fast-rate-limit-fallback's
  `account-usage-compatibility.json` stays as it is.

Append the four candidate digests. Keep every existing digest: hosts run main
and the candidate side by side during rollout.

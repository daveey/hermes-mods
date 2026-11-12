# PR #1300 (wt/hist refusal) compatibility audit

Candidate core: `ab0cdbe07b70ecd7189df691b672378546b3b053` (2 commits on fork main `83f3a262de`).

| File | Accepted (fork main) SHA-256 | Candidate SHA-256 |
|---|---|---|
| `agent/turn_response_check.py` | `d405569356f8b1f5b91e987bd2dab94315da1d9ad9941937e8a0849c2ac2ac1d` | `ef97b2b74c30a35701f2b642f0209dc3296ec085cbea18cd089c22df40a9ac3c` |

Also changed by the PR, not pinned: `agent/turn_truncation.py` (`handle_content_policy_refusal`), new
`agent/turn_refusal_history.py`.

## Findings

- `turn_response_check`: `ResponseCheckVerdict` gains `conversation_history`; `check_api_response`
  takes an optional `system_message` and forwards it to the content-policy refusal handler. When the
  handler returns compacted `messages`/`conversation_history`, they replace the turn's lists before
  the `break`. Only the HTTP-200 content-policy refusal branch changes; retry limits, fallback
  activation, invalid-response handling and restart behavior are unchanged.
- `turn_truncation`/`turn_refusal_history`: a refusal on the turn's first model call (history plus
  only the new user row) now compacts the earlier history once via `agent._compress_context` and arms
  the existing `_retry.restart_with_compressed_messages` restart (the same path overflow recovery
  uses). The configured-fallback try still runs first and is unchanged. The resend goes back through
  the normal turn loop, so the wrapped `perform_api_call` / `interruptible_*_api_call` and the
  request-local account snapshot apply to it as to any other iteration.
- No changes to the client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
  `switch_model`, `interruptible_*_api_call`, or `perform_api_call`. No new credential or route fence
  is introduced; the selected-attempt recovery wrapper still declines content-policy failures (its
  allowlist is rate limit, billing, auth and model entitlement), so it cannot divert the refusal into
  credential rotation.
- `selection.py` is unchanged, so the recovery-wrapper composition digest is unchanged. All existing
  pins are kept for mixed-version rollout.

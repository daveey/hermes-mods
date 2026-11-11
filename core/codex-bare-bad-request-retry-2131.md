# Codex bare Bad Request 400 retry

Patch **2081**.

## Behavior

- For provider `openai-codex`, HTTP 400 whose entire body is `{"detail": "Bad Request"}` (or, with no parsed body, the SDK's exact wrapped message) is classified as retryable `server_error` carrying `error_context["codex_bare_400"]`. Other providers, other keys, descriptive details and error objects keep `format_error`.
- The marker keeps the error off the post-exhaustion auto-recovery ladder, so an always-400 request costs exactly the normal retry cap, not extra recovery cycles.
- Each bare 400 logs one "codex bare-400 retry" warning with attempt n/max, so error detectors can count retries against final failures.

## How it works

`_classify_400` calls `_is_codex_bare_bad_request` before the malformed-tool-args patterns. `turn_api_error.handle_api_error` emits the warning when the marker is present; `turn_recovery_autorecover.ladder_eligible` returns false for it.

## Code

- `agent/error_classifier.py`: `CODEX_BARE_400_MARKER`, `_is_codex_bare_bad_request`.
- `agent/turn_api_error.py`, `agent/turn_recovery_autorecover.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/agent/test_codex_bare_bad_request_400.py`: classifier table (retryable variants; narrow scope for other providers/bodies); conversation-loop tests: one 400 then 200 completes in two requests; an always-400 stops at the retry cap with no ladder cycle.

## Applying

Apply ordinal(s) 2081 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Related routing: [codex-subscription-routing.md](codex-subscription-routing.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

The match is exact on purpose; a changed upstream wording silently returns to `format_error`. The retry only helps a transient server-side reject.

## Could this be a plugin?

No. Error classification and the retry ladder are core agent loop code.

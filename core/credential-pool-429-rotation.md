# Rotate to another pooled key on the first 429 when the rate-limit window is long

Patches:

- `patches/0078-fix-credential-pool-rotate-on-the-first-429-when-the.patch`: agent. When a 429 says the limit lifts more
  than 60 s from now, the credential pool switches to the next key at once instead of retrying the throttled key
  first.

What you see: with two or more keys in a provider's credential pool, a turn that hits a long rate limit on one key
answers from the next key within seconds. Stock Hermes retried the same key once before it rotated. Anthropic can
send `Retry-After: 600`, so that one retry slept the whole 10-minute window while a healthy key sat unused. The turn
looked silent for 10 minutes, then rotated on the second 429 and answered in about 2.5 s. With one key, or with a
short or unknown window, nothing changes.

## Behavior

- **First 429, window longer than 60 s:** rotate now. The log line is `Rate limit window Ns exceeds live wait cap 60s
  — rotating on first 429`. The rotation is labelled `rate limit, long window`.
- **First 429, window of 60 s or less, or no window in the error:** unchanged. Retry the same key once, rotate on
  the second 429.
- **Second 429, usage-limit errors, and keys already marked exhausted:** unchanged. These already rotated at once.
- **Only one key in the pool, or no other key available:** recovery reports no success, as before. The fallback
  chain or the normal wait runs.
- The 60 s threshold is the existing `LIVE_RETRY_WAIT_CAP_S` in `agent/retry_utils.py`. It is not configurable.

## How it works

- All changes are in `agent/agent_runtime_helpers.py`.
- `_recover_rate_limit` used to return "retry the same key" (`(False, True)`) on any first 429 that was not a usage
  limit. It now asks a new helper, `_rate_limit_wait_seconds(error_context)`, how long the provider says to wait. If
  the answer is above `LIVE_RETRY_WAIT_CAP_S`, it calls `rotate_and_swap(429, "rate limit, long window")`, the same
  path the second 429 already used. If rotation fails, it falls back to `(False, True)`.
- `_rate_limit_wait_seconds` reads `error_context["reset_at"]`, parses it with the existing
  `_parse_absolute_timestamp`, and returns seconds from now. It returns None when there is no context, no
  `reset_at`, or the time is already past.
- `reset_at` is filled by the existing `extract_api_error_context`: from body fields, the `Retry-After` header,
  `x-ratelimit-reset`, vendor reset headers, or a delay found in the message text. The patch does not change that
  extraction.
- The `recover_with_credential_pool` docstring now says rate limits rotate at once when the window exceeds a live
  wait.

## Tests

`tests/agent/test_429_long_retry_after_rotates.py` (new), using a real two-key Anthropic pool written to a temp
`auth.json`:

- `test_first_429_with_long_retry_after_rotates_to_sibling`: `Retry-After: 600` returns `(True, False)` and swaps to
  the second key.
- `test_first_429_with_short_retry_after_still_retries_same_credential`: `Retry-After: 5` returns `(False, True)`, no
  swap, and no entry is marked.
- `test_long_retry_after_on_a_sole_credential_does_not_report_recovery`: with one key, `Retry-After: 600` reports no
  recovery and does not swap.
- `test_run_conversation_rotates_without_sleeping_retry_after`: a full `run_conversation` turn. The first call
  raises a 429 with `Retry-After: 600`, the second key answers, and no backoff sleep of 60 s or more happens.

## Applying

Standalone. It touches one function in `agent/agent_runtime_helpers.py` and adds one test file. It needs
`LIVE_RETRY_WAIT_CAP_S` in `agent/retry_utils.py` and `_parse_absolute_timestamp` in the same helpers module. Both
exist in the fork this patch was cut from. If your tree lacks them, the import or call will fail at load time.

## Gotchas

- Rotation goes through `pool.mark_exhausted_and_rotate`, so the throttled key is marked exhausted on the first 429
  now, not the second. The error context (with `reset_at`) is passed along. How long the key stays benched is the
  pool's existing rule; this patch does not change it.
- The decision uses only the time the provider reports. A 429 with no usable reset time keeps the old retry-once
  behavior, even if the real window is long.
- Exactly 60 s does not rotate; the check is strictly greater than the cap.

## Plugin?

No. The choice between retrying and rotating happens inside `_recover_rate_limit` in the agent's error-recovery
path. There is no plugin hook there.

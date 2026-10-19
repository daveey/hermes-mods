# Account-usage refresh claims can't wedge, plus a cache diagnostic

Patch:

- `patches/0139-fix-usage-account-usage-refresh-claims-can-no-longer.patch`: `agent/account_usage_cache.py`, a
  new `diagnostics.account_usage_cache` RPC (`tui_gateway/methods_config.py` + contract + generated contract
  files) and tests.

What you see: a Desktop backend that has been up for hours keeps showing real usage windows for every pooled
Claude/Codex account in the model picker and on the account-usage chip. Before this fix, such a backend could
show every account as `unknown` with no windows until Desktop restarted, while a fresh process on the same home
got real numbers within seconds.

## Behavior

- The picker (`model.options` → `hermes_cli/inventory.py::_apply_usage`) reads usage from a per-process cache.
  It asks for a background refresh per slot, throttled to one try every `REFRESH_AFTER_S` (120 s). A snapshot
  older than `SNAPSHOT_STALE_AFTER_S` (15 min) renders `unknown`.
- Before the fix, a refresh claim (`_inflight`) that was never released blocked that slot for the life of the
  process. After 15 min the slot rendered `unknown` forever. These are the ways a claim could leak, and each one
  is now closed:
  - **The thread fails to start** (`RuntimeError: can't start new thread` under load or thread exhaustion). Every
    claim from that call is released and its try is forgotten, so the next picker open retries right away. A
    warning is logged.
  - **A fetch escapes the per-account worker** (a `BaseException`, or an error outside the fetcher's own
    `except Exception`). Before the fix this stranded every slot queued behind it in the batch. Now an ordinary
    error is logged at debug level and the worker moves on to the next slot. An outer `finally` releases every
    claim in the batch whatever escapes.
  - **The worker computes a different key.** The worker used to rebuild the key inside the thread from the
    current Hermes home. It now releases exactly the key it claimed.
- **Claims expire.** Each claim stores a token and a monotonic time. The worker resets that time when the slot's
  own fetch starts, so a slot queued behind its siblings doesn't look wedged. A claim older than
  `INFLIGHT_EXPIRE_S` (60 s, 4× the 15 s per-call fetch bound) is taken over by the next refresh, and a warning
  is logged. A worker whose claim was taken over cannot release the newer claim, because the token must match.
  The 120 s try throttle still applies, so a wedged slot is retried at most every 2 minutes.
- **Diagnostic.** `diagnostics.account_usage_cache` (JSON-RPC, scoped by `profile`/`session_id` like the other
  config RPCs) and `agent.account_usage_cache.account_usage_cache_diagnostics()` are read-only. For the current
  home they return `inflight_count` and one row per slot: `provider`, `identity_id`, `inflight`,
  `inflight_age_s`, `last_try_age_s`, `snapshot_age_s` and `stale`. They also return the three bounds. They never
  claim, release or expire anything, and no token rides the wire (ids are the `fp:`/`codex:` fingerprints). The
  warnings `account usage: refresh claim for ... expired after Ns inflight; reclaiming` and `account usage: could
  not start ... refresh` in `agent.log` mark the next occurrence.

## How it works

`_inflight` changes from `set[key]` to `dict[key, (token, monotonic)]`. `_claim_refresh(key, now)` returns a token
or `None`. `_touch_refresh`, `_release_refresh(key, token)` and `_abandon_refresh(key, token)` (release and pop
`_last_try`) only act on a matching token, or on any claim when the token is `None`. `refresh_account_usage_async`
and `refresh_account_usage_entries_async` wrap thread creation and start. The entries path also covers its claim
loop, and it stamps `_claim_key`/`_claim_token` into each request it hands the worker. `_refresh_entries(requests)`
keeps its one-argument signature.

## Tests

- `tests/agent/test_account_usage_cache_claims.py` (9 tests). Thread start failure releases the claim on both the
  legacy and the per-account paths. A `KeyboardInterrupt` from the first fetch strands none of the 3 claims in its
  batch. An ordinary error does not stop its siblings. A claim older than the bound is reclaimed. The bound sits
  between 2× the fetch bound and the try throttle. A stale worker's release spares the newer claim. The worker
  releases its claimed keys after a home change. The diagnostic rows are correct, scoped to the current home and
  read-only.
- `tests/tui_gateway/test_diagnostics_account_usage_cache.py`: the RPC result validates against its contract,
  reports a claim that has been wedged for an hour, and leaves the claim in place.
- Existing suites were rerun and pass: `test_account_usage*.py`, `test_model_options_{pool_usage,account_usage,
  pool_usage_reasons}.py` and `tests/tui_gateway/contracts`.
- A real-home probe in a fresh process (not a test file) showed that a forced thread-start failure leaves 0
  claims, and the next call returns real windows for every pooled account.

Whether this was the exact leak in the backends that hit it is unproven. Those processes could not be inspected.
The diagnostic RPC is there to confirm the next occurrence.

## Applying

`git am -3 core/patches/0139-*.patch`. It touches `tui_gateway/contracts/config_free_tier_control.py` and the two
generated contract files, which other contract patches also touch. If they conflict, regenerate the generated files
with `scripts/gen_gateway_contracts.py`. A running backend keeps the old module until it restarts.

## Gotchas

- The diagnostic only exists in backends started after this patch. One that was already wedged recovers on its
  next restart.

Plugin? No. The claim bookkeeping is private module state, and no hook can observe it.

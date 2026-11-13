# Pre-audit: core PR #2232 (Gemini cancel-mid-stream socket shutdown, head a6644ef79c60)

Card t_01a1d500959c2e5c. PR base f24789290998 is current fork main; main's `agent/agent_runtime_helpers.py` is
identical to the base (2f5583394064).

Drifted pins (main digest kept, PR digest added):
- `execution-compatibility.json` `agent/agent_runtime_helpers.py`: 2f5583394064 (main) -> 94780fab321c (PR)

The PR's other files (`agent/aux_provider_handoff.py`: docstring only; `agent/auxiliary_class_routing.py`:
`_close_pair` skips the async Gemini wrapper's close when it wraps the sync leaf; tests) are not pinned by
account-usage or fast-rate-limit-fallback.

What changed in `agent/agent_runtime_helpers.py` (+2/-2): `_iter_pool_sockets` now falls back to
`client._http` (Gemini native client) when `client._client` is missing or falsy, so `force_close_tcp_sockets`
can reach Gemini's httpx pool.

Against selection.py's seams:
- Client factories: untouched. OpenAI and Anthropic clients the main agent builds (including those built for a
  request-local snapshot key) always carry `_client`, so their socket discovery takes the same path as before.
- `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`, `interruptible_*_api_call`,
  `perform_api_call`: untouched. The interrupt path's socket shutdown only gains reach for Gemini clients; it
  reads no credential or route state.
- No new credential or route fence: the change is socket discovery only.
- selection.py unchanged, so recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need
  no refresh.

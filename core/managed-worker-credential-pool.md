# Managed workers keep the credential rotation pool

Patches:
- `patches/1545-fix-gateway-preserve-managed-worker-credential-rotat.patch` (`b8f9678f66`, `fix(gateway): preserve managed worker credential rotation pool`)
- `patches/1546-test-gateway-fence-launch-keys-and-cover-pool-handof.patch` (`af1c8db5e7`, `test(gateway): fence launch keys and cover pool handoff on updater Pythons`)

## Behavior

A managed (out-of-process) Kanban worker used to get only the first bearer token from the runtime resolver. When that account hit a usage limit (HTTP 429), the worker kept retrying the same account, while in-process turns rotated to the next pooled account. Now the managed worker keeps the resolver's `credential_pool` with the bearer, so Anthropic and OpenAI Codex workers recover from a 429 by rotating accounts like in-process turns do.

The pool is attached only when:
- the resolver actually returned one (custom and other non-pool routes get none), and
- no explicit launch key is bound (1546). A bound launch key stays authoritative even if an OAuth resolver also returns that profile's pool. This fixes an xAI OAuth regression where the profile pool would have replaced the launch token.

Fallback routes stay separate: when the resolver answers with a fallback, the primary route gets nothing from it. Deferred class routes are unchanged.

## How it works

`worker_route(policy, construct)` in `gateway/session_worker_construct.py` builds `{api_key, base_url, api_mode}` as before. It adds `credential_pool` from the `route_runtime` result only if `construct['api_key']` is empty and `runtime['credential_pool']` is not `None`. The constructed agent then holds it as `_credential_pool`, and the existing `recover_with_credential_pool` marks the exhausted entry and swaps `api_key` to the next one.

## Code

`gateway/session_worker_construct.py` (`worker_route`).

## Tests

From the patches; **not run for this mirror**.

- `tests/gateway/test_managed_worker_pool_rotation.py`:
  - `test_managed_agent_keeps_resolved_pool`, parametrized over `openai-codex` and `anthropic`: real managed-agent construction, then `recover_with_credential_pool` on a 429 `usage_limit_reached`. Asserts the agent holds the same pool, switches to the second token, and marks the first entry exhausted or cooled down.
  - `test_worker_does_not_invent_pool_for_nonpool_route`: custom route, with and without an explicit key.
  - `test_bound_xai_launch_key_does_not_attach_profile_pool`: keeps the launch token, no pool.
  - `test_fallback_pool_never_attaches_to_primary`.
- `tests/gateway/test_worker_pool_handoff_contract.py`: `WorkerPoolHandoff.test_resolver_pool_survives_for_each_subscription_provider`. A dependency-free `unittest` that loads the module by path, so it also runs on the updater's Python versions. The source owner reported it RED/GREEN on 3.11 and 3.14.

Rotation against live provider accounts is untested here. The source owner keeps that as a deployed live check.

## Applying

Depends on the preceding series (managed worker construction, `route_runtime`, credential pool). Apply the full ordered series with `git am -3 --empty=keep` as described in [README.md](README.md) Applying. Standalone application is not certified.

## Gotchas

- Any non-empty `construct['api_key']` disables pool rotation for that worker, even when the key belongs to a pooled provider.
- Rotation is only as good as the pool. A single-entry pool still exhausts and fails as before.

## Plugin?

No. Managed-worker agent construction is a core gateway path with no plugin hook.

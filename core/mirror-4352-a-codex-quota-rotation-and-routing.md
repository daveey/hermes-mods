# Codex quota benches, singleton recovery and rotated-pool retries

Mirror snapshot 4352; ordinals 4162, 4163, 4164, 4193, 4195.

Entries:

- **4162**: fix(auth): preserve Codex quota benches and rotate past readmitted rows
- **4163**: fix(auth): scope singleton quota recovery to proven row and failure
- **4164**: fix(auth): accept a reopened walled window whose reset moved later
- **4193**: fix(routing): reselect or type a rotated-off subscription pool before the retry
- **4195**: kanban quota hold: report when a fallback route opens, not only the walled route's reset

## Behavior

- **Quota benches survive probes** (4162, 4163, 4164): an open short window used to erase a multi-day Codex weekly wall and hide a healthy account behind a non-retryable "no selected entry" error. Early bench lifts now need evidence for the persisted reset window and the failure generation: positive evidence from an older failure generation is not reused, quota is preserved when adopting a singleton token pair, just-marked failures are reapplied and their row excluded before resync/probes, and early lifts log at INFO. Recovery is scoped to the proven row and failure (unrelated accounts are not cleared; row generation is compared before clearing).
- **Moved resets** (4164): a redeemed banked/upstream reset reopens the weekly window with a later reset than the persisted wall, so evidence is an open window resetting no earlier than the persisted wall (60 s tolerance); a short window resetting before it proves nothing.
- **Rotated-off subscription pools** (4193): when a 429 benches the only entry for a model and the retry reuses an empty selection, `build_request_payload` re-selects a subscription entry (swapping the agent onto it), and `verify_class_runtime` raises the typed `SubscriptionAuthUnavailable` for an auth-churned pool, matching resolution. The generic non-retryable error remains only for pools with neither room nor typed evidence.
- **Quota hold clock** (4195): `kanban_quota_episode.release_at()` reports the earliest wall reset at which dispatch would admit the card via a fallback route, else the episode's own hold, used for the stuck warning, chat wait, pickup `quota_hold` detail and episode notice. Dispatch decisions are unchanged.

## How it works

- `agent/credential_pool.py`, `credential_pool_bench.py`, `hermes_cli/auth_codex.py` and the new `hermes_cli/auth_codex_quota_window.py` implement evidence and generation checks; `fixture/cache/plugin_hooks.json` is re-pinned by 4164.
- `agent/model_routing_runtime.py` and `agent/turn_api_request.py` implement `reselect_class_credential` and the auth-churn rung.
- `hermes_cli/kanban_quota_episode.py`, `kanban_db_dispatch_clock.py` and `kanban_worker_node.py` implement `release_at`.

## Code

Shipped non-test paths (union over these entries):

- `agent/credential_pool.py`
- `agent/credential_pool_bench.py`
- `agent/model_routing_runtime.py`
- `agent/turn_api_request.py`
- `fixture/cache/plugin_hooks.json`
- `hermes_cli/auth_codex.py`
- `hermes_cli/auth_codex_quota_window.py`
- `hermes_cli/kanban_db_dispatch_clock.py`
- `hermes_cli/kanban_quota_episode.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/agent/test_codex_rotation_window.py`
- `tests/agent/test_codex_rotation_window_boundaries.py`
- `tests/agent/test_model_bench_wait_verify.py`
- `tests/hermes_cli/test_auth_codex_quota_probe.py`
- `tests/hermes_cli/test_codex_credential_host_binding.py`
- `tests/hermes_cli/test_kanban_quota_episode_release_at.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Tests for the credential work use stubbed HTTP; real provider behaviour is not exercised.
- `release_at` is informational: a card is still held by dispatch decisions, only the reported deadline changes.
- A reset moving later by less than the tolerance, or a short window resetting earlier than the wall, intentionally leaves the account benched.

## Plugin?

None.

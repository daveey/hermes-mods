# Model class routing (sticky allocator, managed subscriptions)

Patches **0404–0429, 0431–0437, 0440** (source `66d75e8c98`..`7334dc32dd` plus `7d0a99f49b`). Opt-in, profile-local
allocator: a workload's model is `class:deep`, `class:standard` or `class:fast` with provider `auto`, and Hermes
resolves the marker to one exact registered route before any provider request is built. Off by default
(`model_routing.enabled: false`); with it off, legacy concrete-model behavior is unchanged and class requests fail
clearly.

## Behavior

- **Registry.** `model_routing.routes.{deep,standard,fast}` list exact `provider`/`model`/`billing`/`weight`/
  `capabilities` routes. A route gets work only when both `eligible` and `funding_verified` are true (host-specific
  operator attestations). Weights are identical per provider within a class. Billing is `subscription`,
  `credit-funded` or `metered`; metered needs `model_routing.allow_metered`. An empty eligible class fails closed.
- **Sticky allocation.** The choice is stored transactionally in `model-routing.sqlite3` under the profile home and
  stays fixed across turns, tools, restarts, resume and compression lineage (0421). Auth, quota or transport recovery
  may pick another route of the same class only; it never crosses class, billing or capability checks. A failover is
  published only after the new runtime is prepared (0422), and native clients are retired after same-class recovery
  (0428).
- **Entry points carry the class, not a credential.** CLI startup and resume (0406), Desktop/TUI construction and
  resume (0407), Desktop readiness and side jobs (0408), API completions and runs (0409, 0410, 0432), messaging
  (0411, 0427), delegation (0412), cron per-fire construction (0413) and Kanban workers with deep-review classes
  (0414). Each resolves the class at agent construction/recovery (0405) so no legacy provider discovery runs first.
- **Auxiliary calls are fenced.** Streaming and non-streaming auxiliary, direct and vision clients are checked
  against the class (0415, 0417–0419); an auxiliary stream recovers only before any delivery (0416). The main
  request is validated against route capabilities before send (0420). Refresh and reset-turn recovery exclusions are
  fenced (0431).
- **Pins.** An explicit gateway pin clears durable class provenance (0423); a temporary pin or `/model --once` does
  not replace the standing class (0425, 0436, 0437). Caller request overrides survive runtime switches (0440).
- **Managed subscriptions.** `model_routing.enforce_subscription_only` additionally fences concrete pins, saved
  overrides, auxiliary calls and fallbacks to one unambiguous eligible native subscription route (`anthropic`,
  `openai-codex`, `xai-oauth`) and overrides `allow_metered` (0433). Deferred managed routes resolve for compression
  and background work (0435). Gateway hygiene probes use the sticky registry context (0426).
- Patch 0404 adds the allocator and subscription credential fence; 0434 adds the user doc
  `website/docs/user-guide/model-class-routing.md`.

## How it works

`agent/model_routing*.py` (registry, state, request, runtime, restore, managed), `agent/auxiliary_class_routing.py`
and `auxiliary_class_readiness.py`, `agent/agent_runtime_switch.py` / `agent_runtime_once.py`,
`gateway/run_class_routing.py`, `gateway/platforms/api_server_class_routing.py`, `cron/scheduler_class_routing.py`,
`tools/delegate_class_routing.py`, `tui_gateway/class_routing.py`, `hermes_cli/cli_model_routing.py`,
`hermes_cli/kanban_model_tier.py`; config defaults in `hermes_cli/config_defaults_routing.py`.

## Code

The files above plus `agent/credential_pool*.py` (credential fence), `agent/conversation_compression*.py` (lineage),
`gateway/run_model_context.py`, and `docs/model-routing.md`.

## Tests

Run in a merge-base worktree with `scripts/run_tests.sh`. Files added or changed by these patches:
`tests/agent/test_model_routing*.py`, `test_auxiliary_class_*.py`, `test_class_*.py` (compression lineage, failure
and cross-provider wire, refresh fence, request guard), `test_managed_*.py`, `test_runtime_request_overrides.py`;
`tests/cron/test_class_routing.py`, `tests/gateway/test_api_class_*.py`, `test_messaging_class_routing.py`,
`test_class_background.py`, `test_deferred_managed_runtime.py`; `tests/hermes_cli/test_cli_class_routing.py`,
`test_kanban_class_routing.py`, `test_model_routing_runtime.py`; `tests/tools/test_delegate_class_routing.py`;
`tests/tui_gateway/test_class_routing.py`, `test_managed_model_once.py`, `test_once_compression_lifecycle.py`.
This mirror task did not re-run them; the claim here is limited to a clean `git am -3` of the series.

## Applying

Apply 0404–0440 in order after 0445. They depend on earlier patches (kanban tier, credential pool) and on
`kanban_session_activity.py` fixtures adapted in 0393.

## Gotchas

- Class routing never downgrades to another class; deployment and live provider entitlement are separate gates.
- A valid registry does not prove providers are funded on a host. Never put credentials in the registry.
- Untested paths: none claimed beyond the listed test files.

## Plugin?

No. Core-owned.

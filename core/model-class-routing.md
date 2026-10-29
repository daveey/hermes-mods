# Model class routing (sticky allocator, managed subscriptions)

Patches **0411, 0412, 0413, 0414, 0415, 0416, 0417, 0418, 0419, 0420, 0421, 0422, 0423, 0424, 0425, 0426, 0427, 0428, 0429, 0430, 0431, 0432, 0433, 0434, 0435, 0436, 0437, 0438, 0439, 0440, 0441, 0442, 0443, 0446** (source `66d75e8c98`..`7334dc32dd` plus `7d0a99f49b`). Opt-in, profile-local
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
  stays fixed across turns, tools, restarts, resume and compression lineage (0428). Auth, quota or transport recovery
  may pick another route of the same class only; it never crosses class, billing or capability checks. A failover is
  published only after the new runtime is prepared (0429), and native clients are retired after same-class recovery
  (0435).
- **Entry points carry the class, not a credential.** CLI startup and resume (0413), Desktop/TUI construction and
  resume (0414), Desktop readiness and side jobs (0415), API completions and runs (0416, 0417, 0438), messaging
  (0418, 0434), delegation (0419), cron per-fire construction (0420) and Kanban workers with deep-review classes
  (0421). Each resolves the class at agent construction/recovery (0412) so no legacy provider discovery runs first.
- **Auxiliary calls are fenced.** Streaming and non-streaming auxiliary, direct and vision clients are checked
  against the class (0422, 0424, 0425, 0426); an auxiliary stream recovers only before any delivery (0423). The main
  request is validated against route capabilities before send (0427). Refresh and reset-turn recovery exclusions are
  fenced (0437).
- **Pins.** An explicit gateway pin clears durable class provenance (0430); a temporary pin or `/model --once` does
  not replace the standing class (0432, 0442, 0443). Caller request overrides survive runtime switches (0446).
- **Managed subscriptions.** `model_routing.enforce_subscription_only` additionally fences concrete pins, saved
  overrides, auxiliary calls and fallbacks to one unambiguous eligible native subscription route (`anthropic`,
  `openai-codex`, `xai-oauth`) and overrides `allow_metered` (0439). Deferred managed routes resolve for compression
  and background work (0441). Gateway hygiene probes use the sticky registry context (0433).
- Patch 0411 adds the allocator and subscription credential fence; 0440 adds the user doc
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

Apply 0411, 0412, 0413, 0414, 0415, 0416, 0417, 0418, 0419, 0420, 0421, 0422, 0423, 0424, 0425, 0426, 0427, 0428, 0429, 0430, 0431, 0432, 0433, 0434, 0435, 0436, 0408, 0437, 0438, 0439, 0440, 0441, 0442, 0443, 0444, 0445, 0446 in order after 0451. They depend on earlier patches (kanban tier, credential pool) and on
`kanban_session_activity.py` fixtures adapted in 0343, 0361, 0364, 0365, 0366, 0387, 0392.

## Gotchas

- Class routing never downgrades to another class; deployment and live provider entitlement are separate gates.
- A valid registry does not prove providers are funded on a host. Never put credentials in the registry.
- Untested paths: none claimed beyond the listed test files.

## Plugin?

No. Core-owned.

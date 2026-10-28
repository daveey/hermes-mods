# Model tiers: per-task model on delegate_task, default model for Kanban cards

Patches:

- `patches/0151-feat-delegate-per-task-model-provider-pin-on-delegat.patch`: tools. `delegate_task` tasks[] entries
  may pin `model` (and optional `provider`) for that child, behind `delegation.allow_task_model`.
- `patches/0152-feat-kanban-default-model-tier-for-unpinned-cards-ka.patch`: hermes_cli. The dispatcher runs cards
  with no pinned model on `kanban.default_model`, except reviews, retries after a failure and keep-pattern matches.

What you see: routine Kanban cards and subagents run on a cheaper model (Sonnet on the fork), while reviews, hard
retries and cards you pin stay on the profile's model (Opus). Each Kanban run records why in a `model_tier` event.

Patch 0302 fixes eager default-route preflight: only routes selected by a child are resolved.

## Behavior

delegate_task:

- With `delegation.allow_task_model: true`, each `tasks[]` entry may set `model` and `provider`. The pin applies
  to that child only; other tasks keep the call-wide route (`delegation.model`/`provider`, else the parent).
- `model` alone keeps the configured (or inherited) provider. A `provider` different from `delegation.provider`
  drops that block's `base_url`/`api_key`/`api_mode`/`command`/`args` and its model, then resolves through the
  same credential path as `delegation.provider`.
- An unavailable unused default does not veto an explicit per-task provider. A mixed batch still fails atomically if any child needs an unavailable route.
- A non-string pin or a provider that fails to resolve refuses the whole call before any child spawns
  (`Task <i>: ...`).
- Off (upstream default): the schema does not advertise the fields and the model-facing entry points strip them,
  so no model-facing field carries a route (upstream's `test_provider_is_plugin_facing_only` invariant holds for
  the static schema).

Kanban dispatcher (per run, decided at claim time):

1. Card pins `model` (`kanban_create(model=...)`, `--model`): that model, unchanged.
2. `kanban.default_model` empty (default): upstream, the profile's model.
3. Assignee not in `kanban.default_model_profiles` (empty = all): profile model.
4. Review lane (`sdlc-review` loaded): profile model.
5. An earlier run ended `crashed`/`timed_out`/`gave_up`/`failed`, or a reviewer asked for changes
   (`changes_requested`/`review_reopened`): profile model (the second attempt after a failure gets the stronger
   model). `continued`, `blocked` and `reclaimed` runs don't count.
6. Title or body matches a `kanban.default_model_keep_patterns` regex (case-insensitive): profile model.
7. Otherwise `kanban.default_model` (+ `kanban.default_provider` if set).

When `kanban.strong_model` is set, steps 4–6 run on it instead of on the profile model
([kanban-strong-tier.md](kanban-strong-tier.md), patches 0541 0542).

The pin is set on the in-memory claimed task only, so the card row never changes and every run re-decides.

## How it works

- `tools/delegate_tool.py`: `_task_route_cfg` builds a per-task copy of the delegation block, `_resolve_task_routes`
  resolves each through `_resolve_delegation_credentials`, `_build_children` uses each task's `(creds, routing_cfg)`.
  `_TASK_MODEL_PROPERTIES` is added to the schema by `_build_dynamic_schema_overrides` only when allowed;
  `_strip_model_hidden_task_fields` strips the fields otherwise (registry handler and `run_agent` dispatch).
- `tools/delegate_tool_config.py`: `_get_allow_task_model()`.
- `hermes_cli/kanban_model_tier.py`: `resolve_model_tier` / `apply_model_tier` (writes the `model_tier` event).
- `hermes_cli/kanban_db_dispatch.py`: `_dispatch_lane_task` calls `apply_model_tier` after the review-skill
  injection; `_worker_argv` turns the pin into `-m <model> [--provider <p>]` as for a pinned card.
- `hermes_cli/config_defaults.py`: `delegation.allow_task_model`, `kanban.default_model`, `default_provider`,
  `default_model_profiles`, `default_model_keep_patterns`.

## Choosing the keep rule

From a sample of 40 finished cards on one board (14 days), labelled by judgement as "fine on the cheaper model" or
"needs the strong model": 13 needed it (real-time/audio/firmware debugging, security/privacy design, research with
pre-registered statistics or GPU spend, architecture). The rule above keeps 7/40 first runs on the strong model
(6 of the 13, plus 1 false keep); 3 more escalate after a failed cheap run; 4 are missed (hard debugging with no
keyword). `goal_mode` was not used: it marks 25/40 cards, 15 of them fine on the cheaper model. Every card in the
sample had run on the strong model, so the labels are predictions; measure before and after.

## Tests

- `tests/tools/test_delegate_per_task_model.py` (delegate_task tool entry, through the registered handler):
  per-task model overrides `delegation.model` for that child only; a task model beats parent inheritance; a
  provider pin resolves through the credential path; a bad pin refuses the call before spawn; pins are stripped
  when not allowed; the dynamic schema advertises `model`/`provider` only when allowed and the static schema never.
  Unit tests for `_task_route_cfg`. Patch 0302 adds unavailable-default/explicit-provider success and mixed-batch no-spawn tests (registered tool handler). All 12 tests passed; the new success test failed before the fix. Related batch-validation, fallback-matrix, external-provider and request-overrides suites: 58 tests passed together.
- `tests/hermes_cli/test_kanban_model_tier.py` (dispatcher via `dispatch_once`; `kanban_create` tool via
  `_handle_create`): unpinned card gets the default and a `model_tier` event while the row stays unpinned; default
  off = upstream; `kanban_create(model=...)` keeps its pin while an unpinned tool-created card gets the default;
  review lane, retry after a crashed run, keep patterns and profile scoping keep the profile model; a `continued`
  run is not a failure; a bad regex is ignored; the pin reaches the worker argv (`-m`, `--provider`).
- The CLI `hermes kanban create --model` path stores the same `model_override` column as the tool; no separate test.

## Applying

Two independent patches. 0152 needs the upstream Kanban dispatcher (`_dispatch_lane_task`, `_worker_argv`).
Turn them on with:

```yaml
delegation:
  model: claude-sonnet-5-5        # default tier for subagents
  provider: anthropic
  allow_task_model: true          # let the model pin Opus per task
kanban:
  default_model: claude-sonnet-5-5
  default_model_profiles: [default]
  default_model_keep_patterns: [security, adversarial, architecture, design.review, plan.review, diff.review]
```

Add a rule to your agent instructions telling it when to pin the strong model (reviews, architecture, retries).

## Gotchas

- `kanban.default_model` without `default_provider` keeps the profile's provider; a profile on a provider that
  doesn't serve that model will fail its workers. Scope with `default_model_profiles`.
- Keep patterns are matched against the whole body, so a brief that mentions a pattern word incidentally keeps
  the strong model. Prefer specific patterns.
- Leave both `delegation.model` and `delegation.provider` empty to inherit the active parent route; a hardcoded default requires credentials on every host. No automatic fallback is added for unavailable explicit pins.
- `delegation.model` without `delegation.provider` follows the parent's provider; if the parent fell back to a
  different provider, the child asks that provider for the model. Set `delegation.provider` too.
- hermes-sync's shared-config list compare only parses YAML items without spaces or quotes; keep patterns as
  single tokens (`design.review`, not `design review`, no `\b`) or they are re-set on every run.

## Plugin?

No. The dispatcher builds the worker argv in core with no hook between claim and spawn, and the delegate_task
schema and route resolution are core.

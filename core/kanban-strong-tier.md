# Kanban strong tier names its model; cron.model_provider pairs only with cron.model

Patches: 0541 0542.

## Behavior

- `kanban.strong_model` (+ optional `kanban.strong_provider`) names the model that "strong-tier" Kanban
  cards run on while `kanban.default_model` is set. Strong-tier cards are review-lane cards, retries after
  a failed run or `changes_requested`, and cards that match `kanban.default_model_keep_patterns`. Before
  this change they ran on the assignee profile's own model, so changing the profile default silently moved
  every review and retry with it. Unset (`""`, the default) keeps the old behaviour (profile model, or
  `class:deep` when the default is a class selector). An unset `strong_provider` is inferred from the
  model, in the same way as `default_provider`. Explicit card pins and profiles outside
  `default_model_profiles` are unchanged. The `model_tier` event records the strong model.
- `cron.model_provider` now applies only to jobs whose model came from `cron.model`. A job pinned to its
  own model without a provider runs on that model's provider from the static catalog, or on the global
  config when the catalog doesn't know the model or the model is the main `model.default`. Before this, a
  `hermes cron edit --model claude-...` without `--provider` went to the fleet provider (for example
  openai-codex) and failed. A job running on the main model (no `cron.model`) no longer picks up a stray
  `cron.model_provider` either.

## How it works

- `hermes_cli/kanban_model_tier.py` `resolve_model_tier`: the `strong` pair is
  `(strong_model, strong_provider or infer_provider(strong_model))` when `strong_model` is set. It is
  returned by the review, `retry_after_*` and `pattern:` branches.
- `cron/scheduler.py`: `_CronJobConfig.model_source` is set to `job`, `cron`, `main` or `env` by
  `_load_cron_job_config`. It defaults to `cron`, so callers that predate the field keep the old
  semantics. `_default_provider_for(jc)` returns `cron_default_provider` only for `cron`. For `job` it
  returns None when the pin equals the main model, otherwise `infer_provider(model)`. For `main` and `env`
  it returns None. `_resolve_job_runtime` uses
  `job.provider or _default_provider_for(jc)`.

## Code

hermes_cli/config_defaults.py (`kanban.strong_model`, `kanban.strong_provider`),
hermes_cli/kanban_model_tier.py, cron/scheduler.py.

## Tests

- tests/hermes_cli/test_kanban_strong_tier.py: strong model for the review, retry and keep-pattern branches;
  provider inference; unset keeps the old behaviour (including `class:deep`); pins and out-of-scope profiles
  are ignored; a dispatcher review-lane claim gets the pin and its `model_tier` event names the model.
- tests/cron/test_cron_model_provider_pairing.py (full `run_job` path): a provider-less claude pin gets
  `anthropic` even when `cron.model_provider: openai-codex` is set; an unknown-model pin and a pin on the
  main model get None (global config); an unpinned job gets `cron.model_provider`; a main-model job without
  `cron.model` does not get it; the default `model_source` keeps old callers on the fleet provider.

## Applying

Apply the ordered core series through the last listed patch with `git am -3 --empty=keep`, starting at the
fork/upstream merge-base. The patches are not independent. Then set
`hermes config set kanban.strong_model <model>` (and optionally `kanban.strong_provider`).

## Gotchas

- `strong_model` only matters while `kanban.default_model` is set. With no default tier, every card runs on
  its profile model, as upstream does.
- Catalog inference is a guess for models listed under several providers (for example `gpt-5.6-sol` →
  `openai-api`). Give a pinned cron job its own `--provider` when the route matters.
- Provider walls (`kanban-provider-quota-walls.md`) can still re-tier a walled strong model. The event
  reason then reads `review+wall:<provider>`.

## Plugin?

No. This changes core dispatch and cron routing.

## Follow-up patch (0562)

**0562** documents `kanban.strong_model` / `strong_provider` in `website/docs/user-guide/configuration.md` (docs-drift ratchet). No code change.

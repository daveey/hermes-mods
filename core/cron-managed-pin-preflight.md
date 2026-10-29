# Cron pins pass the managed routing registry; gated ticks keep the last verdict

Patch: 0879 (core `a65917ae05`). Cron sibling of [kanban-managed-pin-policy.md](kanban-managed-pin-policy.md).

## Behavior

When `model_routing.enabled` and `model_routing.enforce_subscription_only` are on:

- A cron job whose model (job pin, else `cron.model`, else `model.default`) is not in the routing
  registry is refused by the scheduler preflight: `last_status = blocked_config`, one alert naming the
  pin, where it comes from and the allowed models, no agent built. Before, the provider-key preflight
  swallowed the policy's `ManagedPinRejectedError` as a non-auth error and failed open, so every woken
  run died before the model with "Managed subscription policy rejects unregistered or ambiguous
  concrete pin".
- `hermes cron create --model` / `hermes cron edit --model/--provider` (the `cronjob` tool's create
  and update) refuse such a pin with code `model_pin_not_in_routing_registry` and list the allowed
  models. Only a call that sets the pin is checked, so a stale-pinned job can still be renamed or paused.
- `hermes cron doctor` lists enabled jobs whose model the registry rejects.
- A tick whose gate suppressed the agent (pre-run script printed `{"wakeAgent": false}`, or an
  unchanged monitor) no longer counts as a healthy agent run: `last_status`, `last_error`,
  `failure_streak`, `preflight_alerted` and `last_delivery_error` keep the last real run's values
  (a job with no verdict yet still records `ok`). Before, those once-a-minute ticks reset the streak and
  the alert-once marker, so a job failing on every wake read as `ok` and re-alerted on every wake.
- Hosts without `enforce_subscription_only` behave as before; class selectors are untouched;
  `cron.preflight: false` disables the preflight check (the runtime policy still refuses the pin).

## How it works

`cron/managed_pin.py` resolves the effective model with run_job's precedence and runs the same
`pin_policy()` the runtime runs (`agent/model_routing_managed.py`). `_preflight_job_config` calls it
first (`_preflight_check_managed_pin`), ahead of the provider-key probe. `tools/cronjob_job_args.py`
`_validate_managed_pin` guards create/update (fails open if config can't be read; the preflight still
guards the run). The wake gate and monitor no-change paths set `job["_agent_skipped"]`; the run tail
passes `agent_skipped=True` to `mark_job_run`, whose `_record_run_outcome` keeps the verdict fields.

## Code

- `cron/managed_pin.py` (new): `effective_model`, `rejected_pin`, `rejection_message`, `job_pin_issue`
- `cron/scheduler_preflight.py`: `_preflight_check_managed_pin`, first in `_preflight_job_config`
- `cron/scheduler.py`: `_agent_skipped` at the wake gate and monitor no-change; `agent_skipped` mark kwarg
- `cron/jobs.py`: `mark_job_run(agent_skipped=)`, `_record_run_outcome(agent_skipped=)`
- `tools/cronjob_job_args.py`, `tools/cronjob_tools.py`: `_validate_managed_pin` on create/update
- `hermes_cli/cron.py`: doctor issue for a rejected pin

## Tests

- `tests/cron/test_preflight_managed_pin.py`: run_job blocks an unregistered pin (with and without a
  provider) as blocked_config naming the allowed pins; a registered pin runs; policy off runs; a
  rejected `cron.model` blocks an unpinned job; run_one_job alerts once across two woken runs.
- `tests/cron/test_silent_tick_keeps_streak.py`: after a failed run, a wake-gate tick and a monitor
  no-change tick keep `failure_streak`, `last_status`, `last_error`, `preflight_alerted`; a later real
  success still resets; a fresh job's gate tick reads ok.
- `tests/hermes_cli/test_cron_managed_pin_cli.py`: `hermes cron edit` and `create` refuse an
  unregistered pin (job unchanged / not created); a registered pin and policy off are accepted; an
  unrelated edit of a stale-pinned job is allowed; `hermes cron doctor` lists the rejected pin only.
- `tests/cron/test_managed_entry.py`: a stale paid pin is blocked_config with no send; with
  `cron.preflight: false` the runtime policy still refuses it with no send.
- Untested: the dashboard REST cron create path (it calls the same `cronjob` create, not exercised here).

## Plugin?

No: the preflight, job store and CLI are core.

# Kanban pins pass the managed routing registry at create time

Patch: 0670 (core `abe20b1815`).

## Behavior

When `model_routing.enabled` and `model_routing.enforce_subscription_only` are on, a Kanban card
pinned to a model the routing registry cannot serve is refused when it is created, instead of
crashing at worker startup.

- `kanban_create` (tool), `hermes kanban create --model`, the dashboard `POST /tasks` and
  `hermes kanban set-model` refuse with code `model_pin_not_in_routing_registry`. The message lists
  the allowed names (every eligible subscription route's model) and says to leave the model out
  (unpinned cards run on the profile default).
- A card that already has such a pin in the DB is blocked (kind `assignment`) by the dispatch guard
  before spawn. The guard hold is exempt from the hold cap, like `model_provider_mismatch`. Dry-run
  reports it without writing.
- A worker that still hits the policy at startup (`ManagedPinRejectedError`) exits EX_CONFIG (78),
  so the dispatcher blocks the card after one spawn instead of retrying an exit-1 "crash" until triage.
- `kanban_create`'s `model` description now says: leave this out unless you need another tier; the
  name must be in this host's model routing registry when one is enforced.
- Hosts without `enforce_subscription_only` behave as before. Class selectors (`class:deep`) and
  unpinned cards are untouched. A non-profile assignee lane (its own executor) is not checked.

## How it works

`validate_model_assignment` (`hermes_cli/kanban_model_assignment.py`) loads the assignee profile's
effective config (or the current home's when no assignee), and when `managed_config()` is on, runs
the same `pin_policy()` the worker runs at startup (`agent/model_routing_managed.py`). `pin_policy`
now raises the typed `ManagedPinRejectedError` (a `ModelRoutingError` subclass carrying `allowed`).
Validation maps it to `ModelPinNotInRegistryError`, a subclass of `ModelProviderMismatchError`, so
every existing handler (tool typed error, dispatch `route_model_mismatch`, assignment-repair
completion check) treats it the same way. `set_model_override` now validates too, via
`validate_task_pin` (it previously skipped `validate_model_assignment` entirely). The CLI catches
the error and exits 2. `cli_agent_setup_mixin._ensure_runtime_credentials` sets
`_credentials_terminal` for `ManagedPinRejectedError` only; other routing errors (for example
credentials unavailable for a valid pin) stay retryable.

## Code

- `agent/model_routing_managed.py`: `ManagedPinRejectedError`, `allowed_pins()`
- `hermes_cli/kanban_model_assignment.py`: `ModelPinNotInRegistryError`, `_assignee_config`, `_check_managed_pin`, `validate_task_pin`
- `hermes_cli/kanban_db.py`: `set_model_override` calls `validate_task_pin`
- `hermes_cli/kanban_db_dispatch.py`: `model_pin_not_in_routing_registry` in `_GUARD_HOLD_EXEMPT_REASONS`
- `hermes_cli/kanban.py`: CLI create catches `ModelProviderMismatchError` (exit 2)
- `hermes_cli/cli_agent_setup_mixin.py`: terminal exit for `ManagedPinRejectedError`
- `tools/kanban_tools_schemas.py`: `model` description

## Tests

- `tests/hermes_cli/test_kanban_managed_pin_policy.py`: create refuses a retired pin with and without
  a provider (lists allowed names, no row); a registered pin is accepted; a registered model on an
  unregistered provider is refused; a class selector and an unpinned card are untouched; policy off
  is unchanged; `set_model_override` refuses; the tool returns the typed code; the schema description
  mentions the registry; dispatch blocks a bad DB pin without spawning (ready and review lanes,
  hold-cap exempt); dry-run reports without writing; worker startup rejection sets
  `_credentials_terminal` while another routing error does not; `pin_policy` raises the typed error
  with the original message and `allowed`.
- `tests/hermes_cli/test_kanban_create_entry_parity.py::test_every_create_path_refuses_a_pin_the_managed_registry_rejects`
  covers the tool, CLI and REST create paths.

This mirror did not re-run those tests. The commit adds them; the implementer recorded them red
before the change and green after, including `test_kanban_model_assignment`, `test_kanban_model_override`,
`test_kanban_pin_provider`, `test_cli_provider_resolution` and `tests/*/test_kanban*.py`.

## Applying

`git am -3 core/patches/0670-*.patch` after 0666 (the spaced-pin guard), which touches the same
`kanban_db` create path. 0669 also touches `kanban_db.py` (worker context only); apply 0669 first
so the context lines match this series.

## Gotchas

- The worker's own `-m` pin path is unchanged. `hermes -m <bad pin>` outside Kanban still fails at
  startup (exit 1 for non-workers).
- Dispatch with no `kanban.orchestrator_profile` holds a family mismatch under the guard rather than
  creating a supervisor repair card. A registry-refused pin that is not a family mismatch is cleared
  instead (0711), and that same tick dispatches on the profile default.

## plugin?

No. The check must run inside `kanban_db.create_task` and the dispatcher's guard, which plugins cannot wrap.

Skill pins are a separate check: [kanban-skill-pin-validation.md](kanban-skill-pin-validation.md) (0681).

## Follow-up patch (0711)

**0711** (fork `60466c1011`): a stored pin the routing registry refuses, and that is not a vendor/provider family mismatch, is cleared when no supervisor can take the assignment block.

`route_model_mismatch` still tries `block_task(kind='assignment')`. On `AssignmentRoutingError`, if the error is `ModelPinNotInRegistryError` and `_family_mismatch` is false, `clear_refused_pin` compare-and-swaps that exact pin to NULL, appends `model_override_cleared`, comments, and returns None so this tick dispatches on the profile default. It never substitutes another model or a paid provider. A concurrent `set-model` that changes the pin wins the compare-and-swap (clear returns false). Dry-run does not write. A family mismatch, a registered pin, and a pin the registry also refuses that fails the family check are left on the old path.

Tests in `tests/hermes_cli/test_kanban_refused_pin_clear.py`:

- `test_refused_pin_with_no_supervisor_is_cleared_and_dispatched_same_tick`
- `test_family_mismatch_with_no_supervisor_keeps_existing_path`
- `test_family_mismatch_pin_that_registry_also_refuses_is_not_cleared`
- `test_dry_run_tick_changes_nothing`
- `test_registered_pin_is_untouched`

The CLI `hermes kanban set-model` path is not what this patch changes. Create-time refusal stays 0670.

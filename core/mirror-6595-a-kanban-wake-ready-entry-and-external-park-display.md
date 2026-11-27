# Kanban wake notices, ready-entry registry and external-park display

Patches: 5998, 6045, 6046, 6080, 6092.

## Behavior

- Unproven wake notices de-duplicate by task and wake_at, so a custody re-park with the same backstop does not re-open passive notice eligibility.
- Eight missing wake kinds are registered as ready-entry clocks so parked time is not counted as ready-queue time; conditional kinds only reset the clock when they name status=ready, and the chat-wait prefilter uses the shared conditional predicate.
- A failing immediate wake confirmation records both return codes and clipped outputs; after three consecutive failed pairs the card is woken once with a dispatcher repair comment.
- Timer and deploy/clock parks show as waiting, not running, unless a fresh owner watch explicitly reports a running job.

## How it works

kanban_wake_unproven, kanban_ready_events, kanban_wake_check/confirm and kanban_chat_wait carry the notice logic; kanban_external_activity and the shell adapter set display state; the desktop external-run UI renders the waiting label.

## Code

Paths touched by these entries (non-test):

- `apps/desktop/src/plugins/kanban/external-run-ui.tsx`
- `hermes_cli/kanban_admission_report.py`
- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_chat_wait.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_diagnostics.py`
- `hermes_cli/kanban_external_activity.py`
- `hermes_cli/kanban_external_adapters/shell.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_ready_events.py`
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_wake_confirm.py`
- `hermes_cli/kanban_wake_unproven.py`
- `hermes_state_outcome.py`
- `tools/kanban_tools_schemas.py`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `apps/desktop/src/plugins/kanban/external-run-ui.test.tsx`
- `tests/hermes_cli/test_kanban_external_adapters.py`
- `tests/hermes_cli/test_kanban_external_review.py`
- `tests/hermes_cli/test_kanban_external_watch.py`
- `tests/hermes_cli/test_kanban_ready_clear_notice.py`
- `tests/hermes_cli/test_kanban_ready_entry_registry.py`
- `tests/hermes_cli/test_kanban_replica_wait_eta.py`
- `tests/hermes_cli/test_kanban_session_outcome_tasks.py`
- `tests/hermes_cli/test_kanban_wait_eta.py`
- `tests/hermes_cli/test_kanban_wake_confirm.py`
- `tests/hermes_cli/test_kanban_wake_unproven.py`
- `tests/hermes_state/test_session_status_refresh.py`
- `tests/tui_gateway/test_session_outcome_notices.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- The confirmation counter persists on the local execution row; the generation column is a per-attempt claim and is validated before use.
- Exit code 1 from a watch means only 'not yet'.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban wake bookkeeping and state display inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.

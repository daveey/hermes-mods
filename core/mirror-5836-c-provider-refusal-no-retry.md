# Kanban: provider content refusals stop automatic retries (mirror 5644–5649)

## Behavior
- A provider content-safety refusal is a stop-for-review outcome, not a worker crash: the first charged failure goes through gave_up/triage and is not retried automatically.
- Refusals are excluded from systemic worker-crash fix-card creation and from routing/pin changes.
- Notices name provider, model, run and the first 200 characters of the message, say it is not a crash and will not auto-retry, and point to manual review, re-scoping or archival.
- Generic 'refused to' prose and operational (infrastructure) refusals are not treated as provider refusals; refusals take priority over protocol exemptions; retained-history notice tails are recognized.

## How it works
The shared `classify_failure` function is reused for accounting and notices. `kanban_failure_accounting.py` and `kanban_db_dispatch_crash_accounting.py` force the refusal outcome; `kanban_failure_summary.py` renders and recognizes canonical and retained notice text. A message-boundary regression guards against long preambles.

## Code
- `hermes_cli/kanban_failure_accounting.py`
- `hermes_cli/kanban_failure_summary.py`
- `hermes_cli/kanban_db_dispatch_crash_accounting.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_provider_refusal.py::test_first_refusal_stops_and_uses_shared_notice_classifier` checks triage, one failure charge and no second retry; `test_infrastructure_failure_stays_uncharged` and `test_nonrefusal_keeps_retry_budget` are controls. `tests/hermes_cli/test_kanban_refusal_routing.py::test_prior_refusal_does_not_change_route` checks unchanged routing. Gateway/TUI notice assertions are in `tests/gateway/test_kanban_failure_notice_gateway.py` and `tests/tui_gateway/test_kanban_failure_notice_text.py`. Live provider interaction is untested here; refusal output is fixture input.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Match on the exact provider message shape; broadening the regex risks swallowing real crashes.
- Operational refusals keep their infrastructure accounting exemption.

## plugin?
No. Core change.

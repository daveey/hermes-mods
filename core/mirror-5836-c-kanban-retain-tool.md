# Kanban: kanban_retain with first-wins transport custody (mirror 5679–5683)

## Behavior
- A registered `kanban_retain` tool retains a worker's own admitted run with durable stop-only decision custody: first-wins transport, canonical evidence confirmation, exact local claim settlement.
- Refusal and scope restrictions, cumulative spend, source custody and incomplete dependency state are preserved.
- Definitive Board rejection separates immutable custody from observer-held state so max-runtime and crash handling still apply (one ordinary fallback release is reserved); storage_error replies keep unknown custody and exact replay.

## How it works
`kanban_retain*.py` modules hold local, transport, safety and receipt logic; `kanban_refusal_receipt.py`/`kanban_spend_decision.py` authenticate receipt-derived restrictions during safety supersession; sync wire/client/hub routes carry the request; `tools/kanban_retain_tools.py` registers the tool in `toolsets.py`.

## Code
- `tools/kanban_retain_tools.py`
- `hermes_cli/kanban_retain.py`
- `hermes_cli/kanban_retain_transport.py`
- `hermes_cli/kanban_retain_safety.py`
- `hermes_cli/kanban_failure_release.py`
- `hermes_cli/kanban_sync_wire.py`
- `toolsets.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_retain_local.py::test_registered_retain_ends_exact_run_without_dependency_or_window_reset` exercises the registered local tool; `test_kanban_retain_process.py::test_actual_registered_tool_rc0_is_not_protocol_violation` covers a child process. `test_kanban_retain_transport.py::test_automatic_release_and_retain_first_committed_command_wins` checks custody ordering. `test_kanban_retain_receipt.py::test_confirmation_fails_closed_without_canonical_terminal_proof` checks independent readback requirements; `test_kanban_retain_reply_order.py::test_repeated_storage_refusals_keep_recoverable_custody_and_diagnostics` checks unknown custody and exact replay. Canonical service responses are fixture evidence; a deployed Board service is untested by these cases.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. The Board-side counterpart must be deployed before the tool emits.

## Gotchas
- Scope is the first slice only; universal and historical backfill are separate.
- Keep the tool schema inside the tool-surface contracts.

## plugin?
No. Core change.

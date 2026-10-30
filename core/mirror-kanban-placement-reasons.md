# Local Kanban placement explains its decision

## Behavior

Patch **1112** returns placement metadata for ordinary local `kanban_create` decisions that previously omitted it: `{where: 'local', why: ...}`. An explicit local pin can also return a note identifying where the card would run without that pin. The patch explains placement; it does not change the placement decision.

## How it works

`_place_remote` delegates local result shaping to `_local_placement`. Deferred and `runs_on` decisions retain their specialized metadata; marker decisions retain a remedy. Plain, pinned, and unmet-need local decisions now retain their reason, and ordinary local decisions are logged at INFO. `_marker_placement_fields` promotes an optional note/remedy to the create result.

For a caller local pin, `_local_placement` reevaluates the same view with `placement='auto'` to produce an advisory note when an alternate destination exists. Machine pins rewritten to local pass `note_local_pin=False`, avoiding that advisory for explicit machine placement.

## Code

- `hermes_cli/kanban_placement.py`: `PINNED_LOCAL_WHY`, `local_pin_note`.
- `tools/kanban_tools.py`: `_place_remote`, `_handle_create`.
- `tools/kanban_tools_placement.py`: `_local_placement`, `_marker_placement_fields`.

## Tests

Tests identified from source, **not executed in this documentation pass**:

- `tests/hermes_cli/test_kanban_placement.py::test_local_pin_returns_its_why_and_the_no_need_note`: result, advisory, local creation, and INFO log.
- Same file: `test_local_pin_with_a_local_marker_gets_no_note`, `test_local_pin_with_a_structural_local_need_gets_no_note`, `test_plain_local_decision_returns_its_why`, `test_needs_reason_is_returned`.
- `tests/hermes_cli/test_kanban_worker_node.py::test_tool_create_runs_on_and_placement_to_an_enrolled_machine`: existing machine placement expectations plus the plain local-reason case.

These are tool-handler tests; no CLI or Desktop placement-result test is added by this patch.

## Applying

Replay patch 1112 in the full ordered fork series with preceding patches (`git am -3 --empty=keep` from the documented base). Existing placement routing, machine pins, and tool result helpers are prerequisites. Application and runtime tests were not executed here.

## Gotchas

Callers must no longer interpret absence/presence of `placement` as synonymous with machine placement: inspect `machine`, `runs_on`, `defer`, or `why`. An advisory does not move the card. Remedy and note are optional top-level result fields, not mandatory members of every placement object.

## Plugin

No plugin is introduced. The change belongs to the core create tool's routing/result contract.

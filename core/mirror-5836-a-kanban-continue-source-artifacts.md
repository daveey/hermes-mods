# Kanban continue tool carries source artifacts (mirror 5461)

## Behavior
- The registered `continue` tool accepts `artifacts` (absolute file paths, max 32 per the schema text) alongside `source_path`. The handler now calls a new `stage_continuation` instead of `register_for_ending`, which attaches and then forwards the selection through `heartbeat_source`; the schema field alone would not have carried it.
- Per the schema text, snapshots are staged before continuation publication; `[]` clears the selection and omitting `artifacts` preserves it; it requires this task's bound worker and protocol-2 source. A non-list or non-string `artifacts` raises `ValueError` before `register_for_ending` runs.
- The user guide now says a refused continuation retains successfully staged snapshots for replay, and that symlinks, special files and secret-looking names or bytes are refused (existing rules, restated).

## How it works
`tools/kanban_tools_schemas.py` adds the field; `tools/kanban_tools_source.py` stages selection; `tools/kanban_tools.py` wires the handler. User docs updated in `website/docs/user-guide/features/kanban.md`.

## Code
`tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`, `tools/kanban_tools_source.py`, `website/docs/user-guide/features/kanban.md`.

## Tests
Source: `tests/tools/test_kanban_continue_artifacts.py` (by test names: the registered schema exposes the continue selection only; invalid selections refuse before the initial attach; the selection requires a bound worker; a selection without a source refuses; continuation selection precedes core and survives refusal), and `test_kanban_source_ending_initial.py` is touched without new test functions. Not executed in this mirror pass.

## Applying
Single patch.

## Gotchas
The diff adds no test of receipt contents or blob bytes, so those are not claimed here.

## plugin?
No.

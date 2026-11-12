# skill-triggers

Appends the governing skill's key section to a tool result when its trigger fires (a deferred `tool_call` also matches each inner call), once per session, unless the
session already ran `skill_view(<skill>)`. Table: `triggers.yaml`; override/add rows in
`<HERMES_HOME>/skill-triggers.yaml` (same `id` replaces, `disabled: [id]` removes). A `sections` entry is a heading
prefix or the **bold** lead-in of a paragraph or list item (a list item comes out alone, without its siblings).

Tests: `uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider tests`

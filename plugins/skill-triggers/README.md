# skill-triggers

Appends the governing skill's key section to a tool result when its trigger fires, once per session, unless the
session already ran `skill_view(<skill>)`. Table: `triggers.yaml`; override/add rows in
`<HERMES_HOME>/skill-triggers.yaml` (same `id` replaces, `disabled: [id]` removes).

Tests: `uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider tests`

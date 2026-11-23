# Managed workers: tool credentials require explicit opt-in (mirror 5665–5666, 5726)

## Behavior
- Implicit CLI default tools no longer authorize staging unrelated profile tool API keys into a managed worker.
- Explicit request/platform toolsets are resolved separately and intersected with the child's effective policy; YAML name coercion and legacy `hermes` bundle expansion are shared with the canonical platform-tools reader.
- Staging excludes names recognized by the internal-secret predicate, names beginning `SLACK_` or `TELEGRAM_`, and names ending `_BOT_TOKEN` or `_APP_TOKEN`, even when their tools are selected. This is a named environment filter, not a guarantee about arbitrary credential names.
- The documented host cooldown knob survives managed exec so a zero-cooldown dispatcher is not overridden by a persisted default episode; nightly lifecycle probes provision class registries with dummy loopback credentials.

## How it works
`gateway/session_worker_credentials.py` computes the explicit set; `hermes_cli/tools_config.py` and `toolset_validation.py` supply normalization. Route-key staging and owner-authenticated hydration are unchanged.

## Code
- `gateway/session_worker_credentials.py`
- `hermes_cli/tools_config.py`
- `hermes_cli/toolset_validation.py`
- `tests/e2e/core/kanban/_helpers.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/gateway/test_managed_worker_tool_credentials.py::test_worker_stages_only_explicit_tool_credentials`, `test_explicit_request_precedence_and_surface_resolution`, and `test_explicit_tools_cannot_stage_platform_or_internal_secrets` check the selected environment. `test_managed_worker_env_compat.py::test_exec_preserves_board_cooldown_without_task_identity` checks the cooldown allowlist. `tests/e2e/core/kanban/test_kanban_rate_limit_review.py` uses the loopback fixture service configured by `_helpers.py`; live provider credentials are untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. Toolsets that need credentials must now declare them explicitly.

## Gotchas
- Fixtures relying on the old implicit default must opt in.

## plugin?
No. Core change.

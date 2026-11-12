# Kanban tool blocks retain agent caller attribution

Mirror entries: [2268](patches/2268-fix-kanban-attribute-tool-blocks-to-their-agent-caller-daveey.patch).

## Behavior

- Store and render tool-originated block notices as agent actions, even when no caller identity is available.
- Prefer the guarded owning worker task; otherwise use request-scoped chat identity instead of process-global session environment.

## How it works

kanban_cli_actor exposes a common agent label formatter and tool_actor. tools/kanban_tools passes the owning task only after its lifecycle guard, then supplies that actor to the block operation. A tool with no task or chat identity remains agent, while the existing CLI non-agent fallback remains user.

## Code

- `hermes_cli/kanban_cli_actor.py`
- `tools/kanban_tools.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/tools/test_kanban_block_actor.py` — `test_tool_actor_stored_and_rendered`, `test_tool_without_identity_is_still_agent`, `test_tool_actor_uses_request_scope`, `test_no_request_notice_unchanged`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

The actor string is audit metadata only, never an authorization signal. This patch does not grant one task permission to block another or let a stale process environment impersonate the current chat.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.

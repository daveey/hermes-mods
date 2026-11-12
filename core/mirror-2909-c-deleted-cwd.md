# Deleted launch directories degrade prompt context, not project authority

Patches: **2888, 2889**.

## Behavior

A deleted process working directory no longer crashes context discovery or compaction’s system-prompt rebuild. Project-affecting goal gates and `/init` do not silently retarget a fallback tree.

## How it works

- **2888:** `process_cwd_or_fallback` returns the readable process cwd or the first existing scoped `TERMINAL_CWD`/Kanban workspace, else home, and warns once per process. Agent context, hints, checkpoint lookup, transport and LSP callers use the safe helper. Trace metadata uses `process_cwd` and can remain empty instead of inventing a cwd. Extend the bare-getcwd static guard over agent code.
- **2889:** introduce `resolve_agent_cwd_strict`: configured cwd, else readable launch dir, else None. Goal workspace resolution keeps `(None, None)` when no launch directory exists; `/init` raises a useful `FileNotFoundError` rather than targeting home’s AGENTS.md. Update stale comments.

## Code

- `agent/coding_context.py`
- `agent/context_file_sources.py`
- `agent/lsp/cli.py`
- `agent/prompt_builder.py`
- `agent/runtime_cwd.py`
- `agent/subdirectory_hints.py`
- `agent/tool_executor.py`
- `agent/trace_upload.py`
- `agent/transports/codex_app_server_session.py`
- `agent/system_prompt.py`
- `hermes_cli/goals.py`
- `hermes_cli/init_command.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_deleted_cwd_prompt.py` simulates deleted getcwd, checks fallback priority/warn-once, context-source discovery, compaction prompt rebuild and strict goals/init controls. `test_deleted_process_cwd.py` extends the static bare-getcwd guard.

- `tests/agent/test_deleted_cwd_prompt.py`
- `tests/tools/test_deleted_process_cwd.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

Context degradation does not authorize running gates or writing files in another project. The trace path deliberately differs from prompt fallback. The patch does not recreate a deleted workspace or recover its files.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

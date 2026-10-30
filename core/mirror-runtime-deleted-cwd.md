# A deleted process working directory no longer crashes execute_code or file tools

Patches: 1118.

## Behavior

- `execute_code` succeeds when the process cwd was removed (a worktree shipped or garbage-collected) instead of failing with `dispatch error: [Errno 2]`.
- File-tool base directory resolution survives a deleted process cwd.
- `agent.runtime_cwd.process_cwd()` returns `""` on `FileNotFoundError`/`PermissionError`, and tool consumers use it.

## How it works

`_resolve_child_cwd` used a bare `os.getcwd()` before its own staging-dir fallback. `process_cwd()` is the guarded accessor; `terminal_tool_config._safe_getcwd` is built on it, and `code_execution_env`, `file_tools_paths`, `mcp_tool_config`, `file_operations_search`, `process_registry` and `environments/local` call it. An AST guard fails on any new unguarded `os.getcwd()` under `tools/`.

## Code

- `agent/runtime_cwd.py`
- `tools/code_execution_env.py`, `tools/file_tools_paths.py`, `tools/mcp_tool_config.py`, `tools/file_operations_search.py`, `tools/process_registry.py`, `tools/environments/local.py`, `tools/terminal_tool_config.py`

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. `tests/tools/test_deleted_process_cwd.py`: `test_resolve_child_cwd_skips_deleted_process_cwd_without_terminal_cwd`, `test_resolve_child_cwd_skips_deleted_process_cwd_with_dead_terminal_cwd`, `test_execute_code_succeeds_with_deleted_process_cwd`, `test_file_tools_base_dir_survives_deleted_process_cwd`, `test_process_cwd_helper_returns_empty_when_deleted`, `test_process_cwd_helper_returns_live_cwd`, `test_no_bare_os_getcwd_in_tools`. The `terminal` tool path is not covered by a named test.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

The AST guard covers only `tools/`. An empty-string cwd must be handled by callers (they fall back to staging or configured dirs).

## Could this be a plugin instead?

No: it fixes core tool path resolution.

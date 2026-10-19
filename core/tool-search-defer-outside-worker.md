# Tool Search: defer worker-lifecycle tools in chats only (patch 0145)

Patch:

- `patches/0145-feat-tool_search-defer_outside_kanban_worker-defer-w.patch`: backend (`tools/tool_search.py`,
  `hermes_cli/config_defaults.py`, new test `tests/tools/test_tool_search_defer_outside_worker.py`).

What you see: interactive chats (Desktop, CLI, cron, one-shot `hermes chat -q`) no longer carry the full schemas of
the Kanban worker-lifecycle tools on every turn; they appear only as names behind `tool_search`. Dispatcher-spawned
Kanban workers, which call those tools constantly, still get them as ordinary direct tools.

## Behavior

- New config `tools.tool_search.defer_outside_kanban_worker` (YAML list of tool names, default `[]` = upstream
  behavior).
- When `HERMES_KANBAN_TASK` is unset (chats, cron, one-shot), the listed names are added to the deferred set on top
  of `tools.tool_search.defer` (or upstream's default deferred set when `defer` is unset).
- When `HERMES_KANBAN_TASK` is set (a worker spawned by the Kanban dispatcher), the list is ignored and those tools
  stay direct.
- `tools.tool_search.eager` still wins: a name in both `eager` and this list stays direct everywhere.
- A non-list value (e.g. a bare string) logs a warning and is ignored (treated as empty).
- The fork's shared config (every host) lists the five lifecycle tools: `kanban_heartbeat`, `kanban_complete`,
  `kanban_block`, `kanban_request_review`, `kanban_request_changes`. `kanban_show`, `kanban_create`,
  `kanban_comment` etc. are unaffected.
- Measured by the authoring session: one-shot fixed input went from 37,878 to 34,128 tokens on the MacBook with
  those five tools listed (about 3.7k tokens saved per call in every non-worker session).

## How it works

- `ToolSearchConfig` gains `defer_outside_kanban_worker: frozenset`, parsed in `from_raw` by the new helper
  `_name_set(raw, key)` (stripped names; non-list warns and returns empty).
- `ToolSearchConfig.effective_defer_tools` (the set every deferral decision reads) returns
  `base | defer_outside_kanban_worker` when the list is non-empty and `_in_kanban_worker()` is false, else `base`.
- `_in_kanban_worker()` is `bool(os.environ.get("HERMES_KANBAN_TASK"))`, the variable the dispatcher exports to
  every worker it spawns. It is read at decision time, so the same config file serves chats and workers.
- `hermes_cli/config_defaults.py` adds `"defer_outside_kanban_worker": []` under `tools.tool_search`.

## Code

- `tools/tool_search.py`: `ToolSearchConfig.defer_outside_kanban_worker`, `ToolSearchConfig.effective_defer_tools`,
  `ToolSearchConfig.from_raw`, `_in_kanban_worker`, `_name_set`.
- `hermes_cli/config_defaults.py`: default `[]`.

## Tests

`tests/tools/test_tool_search_defer_outside_worker.py` (5 tests, all pass on aa953642a9):

- `test_default_is_upstream`: the shipped default is `[]` and `effective_defer_tools` equals `defer` unchanged.
- `test_chat_defers_lifecycle_tools`: with `HERMES_KANBAN_TASK` unset the listed tools join the deferred set and
  `is_deferrable_tool_name` defers them, while `kanban_show` stays direct.
- `test_worker_keeps_lifecycle_tools_direct`: with `HERMES_KANBAN_TASK=t_123` the set is just `defer` and none of
  the listed tools is deferred.
- `test_eager_still_wins`: `kanban_complete` in `eager` stays direct; `kanban_heartbeat` is deferred.
- `test_scalar_value_is_ignored`: a bare string value yields an empty extra set.

The token measurement above is from a manual one-shot run, not an automated test. The end-to-end path (a real chat
reaching a deferred lifecycle tool through `tool_call`, a real dispatcher-spawned worker seeing it direct) is
untested by a dedicated test.

## Applying

Core-only. Set the list in config (fork hosts get it from `shared-config.json`):

```yaml
tools:
  tool_search:
    defer_outside_kanban_worker:
      - kanban_heartbeat
      - kanban_complete
      - kanban_block
      - kanban_request_review
      - kanban_request_changes
```

Takes effect in new sessions/backends on the new core. Hosts not on the fork core ignore the key.

## Gotchas

- Plugin and MCP tools don't need this list: they are already deferred by default (`is_deferrable_tool_name`
  defers any non-core toolset). The list matters for core tools such as the Kanban toolset.
- A chat that calls a deferred lifecycle tool directly by name is rejected and must go through `tool_call` (name known, no search needed). Agent instructions that tell chats to call
  e.g. `kanban_complete` should say "via `tool_call`".
- Anything that runs agent code with `HERMES_KANBAN_TASK` exported but is not a worker (e.g. a manual shell that
  copied a worker's env) also keeps the tools direct.

## Plugin?

No: the deferred set is computed inside core `tool_search` before any plugin hook can change the tool list, and
plugins cannot change how core tools are deferred.

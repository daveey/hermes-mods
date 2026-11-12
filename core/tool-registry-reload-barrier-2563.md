# Tool calls and tool lists wait out a plugin force-reload

Patches: **2493, 2531**.

## Behavior

A forced plugin rediscovery unloads every plugin tool and guard hook of a home, then registers them again; this takes seconds. During that window a `tool_call` on another thread answered "'<tool>' is not a known tool name" for a name `tool_search` had just returned, `registry.dispatch` answered "Unknown tool", `tool_describe` reported not found, and a tool list built then lacked the plugin's tools for the whole session. Now these readers wait for the pass to finish (bounded at 60 s) and then resolve against the re-registered tools. Handlers are never invoked before the wait, so nothing runs twice. A plugin that fails to come back is refused with the real error once the pass ends.

## How it works

- New `tools/registry_reload.py`: `reload_barrier(enabled)` marks a Hermes home as mid-reload and bumps a per-home epoch; `wait_for_reload()` blocks while a pass for that home is active (`RELOAD_WAIT_SECONDS = 60`); `settle_reload_since(epoch)` reports whether a pass ran or started since an epoch was read.
- `PluginManager.discover_and_load` wraps the unload-to-re-register span in `reload_barrier(force)`, so only forced passes mark the home.
- `model_tools._handle_function_call` calls `wait_for_reload()` before the bridge, records the epoch, and runs the bridge read again once when a pass overlapped a bridge read that produced a refusal. `ToolRegistry.dispatch` re-reads once after a miss if it had to wait.
- The reloading thread and loader workers that copy its context (a ContextVar) never wait; a pass in another home is not waited on.
- `get_tool_definitions` calls `wait_for_reload_off_loop()`: the same wait, but it returns immediately on a thread running an asyncio event loop (the gateway's MCP-reload refresh of cached agents runs from async handlers), using the registry as it is.
- The TUI's `tools.show` is added to the long-handler set so the wait runs on the RPC pool, not the RPC reader.
- A new-session agent build was already serialised by the discovery lock taken in `discover_plugins`; a test now covers it. Related: [tool-search-eager.md](tool-search-eager.md).

## Code

`tools/registry_reload.py` (new), `hermes_cli/plugins.py`, `model_tools.py`, `tools/registry.py`, `tui_gateway/methods_tools.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_registry_reload_barrier.py` holds a real `PluginManager` force reload open inside a plugin's `register()` and drives `model_tools.handle_function_call` (`tool_call`, `tool_describe`), `registry.dispatch`, `get_tool_definitions` and a real `AIAgent` build; it also covers failed-reload release, other-home independence, owner/worker no-wait, the wait bound and event-loop skipping. `tests/tui_gateway/test_tools_readback_scope.py` asserts `tools.show` is in `_LONG_HANDLERS`. The gateway MCP-reload handlers and an actual RPC `tools.show` call are untested; the event-loop path is exercised through `asyncio.run` only.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The wait is bounded: past 60 s the caller proceeds on the registry as it is, with a warning.
- Event-loop threads never wait, so a tool list built there mid-pass can still lack plugin tools.
- Only `force=True` discovery passes mark the barrier.

## Plugin

Core change; no plugin required.

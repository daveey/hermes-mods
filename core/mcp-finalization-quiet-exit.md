# MCP: quiet shutdown while the interpreter finalizes

Patches: 1693 1702

- `patches/1693-fix-mcp-drop-loop-exception-reports-while-the-interp.patch`: the MCP loop's exception handler drops
  reports during interpreter finalization.
- `patches/1702-fix-mcp-skip-transport-recovery-during-interpreter-f.patch`: an MCP server run loop does not try
  transport recovery during interpreter finalization.

Related: [mcp-lazy-ignore-ttl.md](mcp-lazy-ignore-ttl.md) (1697) is the other MCP change in this range.

What you see: a managed worker or agent process that exits with MCP tasks still pending exits quietly. Its log no
longer fills with `--- Logging error ---` tracebacks (asyncio `Task was destroyed but it is pending!` reports logged
after the import machinery is gone), which a crash detector had read as a crash storm.

## Behavior

- **1693:** `tools/mcp_tool_loop._mcp_loop_exception_handler` returns immediately when `sys.is_finalizing()` is true,
  dropping every report. Otherwise behavior is unchanged: the benign `RuntimeError("Event loop is closed")` from httpx
  finalizers is suppressed, and everything else goes to `loop.default_exception_handler`.
- **1702:** in `tools/mcp_tool_server_run.MCPServerRunMixin.run`, when the transport (`_run_stdio` / HTTP) raises and
  `sys.is_finalizing()` is true, the loop clears `self.session` and breaks without calling `_on_transport_error`. Late
  GC can close a retained run coroutine after the loop and imports are gone, and error classification itself needs
  live imports. Before finalization, a transport failure still goes to `_on_transport_error(exc, budget)` for
  reconnect.
- In both cases the existing cleanup still runs (`session = None`, stdio child PID set cleared).

## How it works

Both modules import `sys` and add a single `sys.is_finalizing()` check: at the top of the exception handler (1693), and
in the run loop's `except Exception` branch after `self.session = None` and before `_on_transport_error` (1702).
Neither changes when tasks are cancelled or how servers are shut down in a normal exit.

## Code

`tools/mcp_tool_loop.py` (1693), `tools/mcp_tool_server_run.py` (1702).

## Tests

- 1693: `tests/tools/test_mcp_stability.py::TestMCPLoopExceptionHandler::test_drops_reports_while_interpreter_finalizes`:
  with `sys.is_finalizing` patched true, a `Task was destroyed but it is pending!` context does not reach
  `default_exception_handler`; unpatched, the same context is forwarded once.
- 1702: `tests/tools/test_mcp_run_finalization.py` (new):
  - `test_transport_failure_recovers_only_before_finalization` (`finalizing` false/true): a stdio run raising
    `RuntimeError("no running event loop")` calls `_on_transport_error` once with that exception before finalization
    and never during it; `session` is `None` and `_stdio_child_pids` is empty either way.
  - `test_retained_run_coroutine_is_quiet_at_real_interpreter_exit`: a subprocess suspends a real `run()` coroutine,
    disables GC and exits, so the coroutine is closed at real interpreter teardown (not a mock of
    `sys.is_finalizing`). The test asserts exit code 0, stdout `suspended`, and empty stderr.

The mirror pass did not rerun these tests.

Untested: 1693 has no real-interpreter-exit test (only the patched `sys.is_finalizing`); the HTTP transport branch of
1702 (the tests use `_is_http() == False`); a real managed-worker exit with live MCP servers.

## Applying

Standalone on a core that has `tools/mcp_tool_loop.py::_mcp_loop_exception_handler` and the `MCPServerRunMixin` split
(`tools/mcp_tool_server_run.py`). 1693 and 1702 are independent of each other.

## Gotchas

- During finalization the handler drops **all** loop exception reports, not only pending-task ones. A real error
  raised during interpreter teardown will not be logged by this handler.
- These are shutdown-only changes. A process that hangs at exit for other reasons (for example a stdio child that
  ignores termination) is not fixed by them.

## Plugin?

No. The MCP event loop and server run loop are core tool infrastructure; plugins cannot install an exception handler
on that private loop or alter its recovery loop.

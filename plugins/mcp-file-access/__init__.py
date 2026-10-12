"""mcp-file-access: approval dialog for Chrome DevTools MCP file access. See core.py."""

from . import core


def register(ctx):
    # Order matters: gate records the call it asked about; prepare only stages recorded calls.
    ctx.register_hook("pre_tool_call", core.gate)
    ctx.register_hook("pre_tool_call", core.prepare)
    ctx.register_hook("post_tool_call", core.finish)
    ctx.register_hook("transform_tool_result", core.rewrite_result)

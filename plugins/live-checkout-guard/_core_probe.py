"""Test helper: run the plugin inside the real Hermes core (not imported by the plugin).

Usage: <hermes runtime python> core_probe.py <hermes-agent dir> <live> <wt> <other>
Registers the plugin through a real ``PluginContext`` on the real plugin manager, lets the real terminal tool
persist a session cwd with ``cd``, then fires the real ``pre_tool_call`` dispatch (and the execute_code RPC dispatch
path) with that task id. Prints one JSON object of results.
"""

import importlib.util
import json
import sys
from pathlib import Path

core, live, wt, other = sys.argv[1:5]
sys.path.insert(0, core)

from hermes_cli import plugins as hp  # noqa: E402
from hermes_cli.plugins_manifest import PluginManifest  # noqa: E402
from tools.terminal_tool import get_session_cwd, terminal_tool  # noqa: E402

spec = importlib.util.spec_from_file_location("live_checkout_guard_core_probe",
                                              Path(__file__).with_name("__init__.py"))
assert spec and spec.loader
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

manager = hp.get_plugin_manager()
manager._discovered = True  # don't load the user's other plugins
ctx = hp.PluginContext(PluginManifest(name="live-checkout-guard", source="user"), manager)
plugin.register(ctx)


def hook(tool, args, task_id):
    msg, _ = hp._dispatch_pre_tool_call_hooks(tool, args, task_id=task_id)
    return bool(msg and "live-checkout-guard" in msg)


out = {}
# A: the real terminal tool records the session cwd after `cd`; no workdir on the later call.
terminal_tool(command=f"cd {live}", task_id="lcg-A")
out["A_recorded"] = get_session_cwd("lcg-A")
out["A_commit_blocked"] = hook("terminal", {"command": "git commit --allow-empty -m x"}, "lcg-A")
out["A_write_rel_blocked"] = hook("write_file", {"path": "x.py", "content": "x"}, "lcg-A")
out["A_status_allowed"] = not hook("terminal", {"command": "git status"}, "lcg-A")
# B: a different session sitting in the worktree is not affected by A's cwd.
terminal_tool(command=f"cd {wt}", task_id="lcg-B")
out["B_recorded"] = get_session_cwd("lcg-B")
out["B_commit_allowed"] = not hook("terminal", {"command": "git commit --allow-empty -m x"}, "lcg-B")
# C: nested hermes_tools call from execute_code goes through the RPC dispatch, which fires pre_tool_call
#    with concrete (already computed) args, so non-literal Python strings are still checked there.
from tools.code_execution_rpc import _default_dispatch  # noqa: E402

res = _default_dispatch("lcg-B")("terminal", {"command": "git commit --allow-empty -m " + "x", "workdir": live})
out["C_rpc_blocked"] = "live-checkout-guard" in str(res)
print(json.dumps(out))

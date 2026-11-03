"""Test helper: the two-call HERMES_HOME leak through the real Hermes core (not imported by the plugin).

Usage: <hermes runtime python> _home_export_probe.py <hermes-agent dir> <register: 0|1>
Call 1 is the incident's `export HERMES_HOME=$(mktemp -d ...)`; call 2 prints the HERMES_HOME it sees (the
session's own home unless call 1 leaked a temp one). Each call
goes through the real pre_tool_call dispatch first and runs through the real terminal tool only when no hook blocks
it, as the agent loop does. Prints one JSON object.
"""

import importlib.util
import json
import sys
from pathlib import Path

core, register = sys.argv[1], sys.argv[2] == "1"
sys.path.insert(0, core)

from hermes_cli import plugins as hp  # noqa: E402
from hermes_cli.plugins_manifest import PluginManifest  # noqa: E402
from tools.terminal_tool import terminal_tool  # noqa: E402

manager = hp.get_plugin_manager()
manager._discovered = True  # don't load the user's other plugins
if register:
    spec = importlib.util.spec_from_file_location("live_checkout_guard_home_probe",
                                                  Path(__file__).with_name("__init__.py"))
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin.register(hp.PluginContext(PluginManifest(name="live-checkout-guard", source="user"), manager))


def call(command):
    msg, _ = hp._dispatch_pre_tool_call_hooks("terminal", {"command": command}, task_id="lcg-home")
    if msg:
        return {"blocked": True, "message": msg}
    return {"blocked": False, "output": json.loads(terminal_tool(command=command, task_id="lcg-home"))["output"]}


out = {"one": call('export HERMES_HOME=$(mktemp -d "${TMPDIR:-/tmp}/coach-home.XXXX"); echo "set $HERMES_HOME"'),
       "two": call('echo "HOME_SEEN=${HERMES_HOME:-}"')}
print(json.dumps(out))

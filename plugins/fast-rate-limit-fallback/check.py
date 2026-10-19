"""Fresh-process check. Run with Hermes's managed Python, not system Python.

Imports run_agent first, as agent processes do: model_tools discovers plugins during that
import, so register() defers the install (importing run_agent from the loader thread would
wait on the main thread's import lock until core's load deadline). The first
pre_api_request then installs it; this script fires that hook once, like a first API call.
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2] / 'hermes-agent'
if not ROOT.is_dir():
    ROOT = Path.home() / '.hermes' / 'hermes-agent'
sys.path.insert(0, str(ROOT))
import hermes_bootstrap  # noqa: E402,F401

started = time.perf_counter()
import run_agent  # noqa: E402  (discovers and loads plugins)
import_seconds = round(time.perf_counter() - started, 2)
from hermes_cli.plugins import get_plugin_manager  # noqa: E402
import agent.agent_runtime_helpers as helpers  # noqa: E402
import agent.turn_recovery as recovery  # noqa: E402
import agent.turn_api_error as api_error  # noqa: E402


def wrappers_active():
    return all(getattr(f, '_fast_rate_limit_fallback', False) for f in (
        getattr(helpers, '_recover_rate_limit', None), recovery.route_classified_error,
        api_error.route_classified_error, run_agent._pool_may_recover_from_rate_limit))


manager = get_plugin_manager()
manager.discover_and_load()  # idempotent; covers entry points that discover before run_agent
plugin = manager._plugins.get('fast-rate-limit-fallback')
after_load = wrappers_active()
manager.invoke_hook('pre_api_request', task_id='check', turn_id='check', session_id='check', model='check')
active = wrappers_active()
account = manager._plugins.get('account-usage')
owner = getattr(helpers.recover_with_credential_pool, '_account_usage_owner', None)
execution = bool(owner and owner.active and not owner.error and owner.patches
                 and all(getattr(obj, name) is wrapper for obj, name, original, wrapper in owner.patches))
account_enabled = bool(account and account.enabled)
print(json.dumps({'plugin': 'fast-rate-limit-fallback', 'enabled': bool(plugin and plugin.enabled),
                  'loader_error': plugin.error if plugin else 'not discovered',
                  'run_agent_import_s': import_seconds, 'active_after_load': after_load,
                  'wrappers_active': active, 'account_usage_enabled': account_enabled,
                  'account_usage_error': account.error if account else None,
                  'execution_installed': execution}, indent=2))
raise SystemExit(0 if plugin and plugin.enabled and not plugin.error and active
                 and (not account_enabled or execution) else 1)

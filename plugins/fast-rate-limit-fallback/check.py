"""Fresh-process check. Run with Hermes's managed Python, not system Python."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2] / 'hermes-agent'
sys.path.insert(0, str(ROOT))
import hermes_bootstrap  # noqa: E402
from hermes_cli.plugins import get_plugin_manager  # noqa: E402

manager = get_plugin_manager()
manager.discover_and_load()
import agent.agent_runtime_helpers as helpers  # noqa: E402
import agent.turn_recovery as recovery  # noqa: E402
import agent.turn_api_error as api_error  # noqa: E402
import run_agent  # noqa: E402

plugin = manager._plugins.get('fast-rate-limit-fallback')
active = all(getattr(f, '_fast_rate_limit_fallback', False) for f in (
    getattr(helpers, '_recover_rate_limit', None), recovery.route_classified_error,
    api_error.route_classified_error, run_agent._pool_may_recover_from_rate_limit))
account = manager._plugins.get('account-usage')
owner = getattr(helpers.recover_with_credential_pool, '_account_usage_owner', None)
execution = bool(owner and owner.active and not owner.error and owner.patches
                 and all(getattr(obj, name) is wrapper for obj, name, original, wrapper in owner.patches))
account_enabled = bool(account and account.enabled)
print(json.dumps({'plugin': 'fast-rate-limit-fallback', 'enabled': bool(plugin and plugin.enabled),
                  'loader_error': plugin.error if plugin else 'not discovered', 'wrappers_active': active,
                  'account_usage_enabled': account_enabled, 'execution_installed': execution}, indent=2))
raise SystemExit(0 if plugin and plugin.enabled and not plugin.error and active
                 and (not account_enabled or execution) else 1)

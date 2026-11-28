"""Canonical read-only request adapter for the core SessionState collector."""
from pathlib import Path


def read_requests(context):
    # Resolve through the same owner-scoped reader as the panel, without mirror
    # reconciliation or operation delivery. Installation IDs remain untouched.
    try:
        from .dashboard import plugin_api
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'user_steps_session_state_api', Path(__file__).parent / 'dashboard' / 'plugin_api.py')
        plugin_api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin_api)
    return plugin_api.panel_requests(context['home'], context['tip'], context['lineage'], reconcile=False)


def register(ctx):
    try:
        from hermes_session_state_requests import register_request_provider
    except ImportError:  # older cores retain the existing panel path
        return
    dispose = register_request_provider('user-steps', read_requests)
    on_unload = getattr(ctx, 'on_unload', None)
    if callable(on_unload):
        on_unload(dispose)

"""Dashboard-only plugin; all logic lives in dashboard/plugin_api.py (loaded by the web server)."""


def register(ctx):  # noqa: D401 - agent-side plugin hook; nothing to register
    return None

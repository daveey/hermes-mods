"""hub-sync: REST only (dashboard/plugin_api.py over hubview.py); no agent hooks or tools."""


def register(ctx):  # noqa: ARG001 - the plugin contributes only its dashboard API
    return None

"""core-update: REST only (dashboard/plugin_api.py); no agent hooks."""


def register(ctx):  # noqa: ARG001 - the plugin contributes only its dashboard API
    return None

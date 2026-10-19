"""blocked-panel: agent-side half is empty; the logic lives in dashboard/plugin_api.py + core.py."""


def register(ctx) -> None:  # no tools/hooks; exists for its dashboard API used by the Desktop panel
    return None

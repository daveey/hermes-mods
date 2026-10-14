"""prompt-coach: agent-side half is empty; all logic lives in dashboard/plugin_api.py
(REST for the desktop plugin) and core.py."""


def register(ctx) -> None:  # no tools/hooks; the plugin exists for its dashboard API + desktop UI
    return None

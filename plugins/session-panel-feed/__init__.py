"""session-panel-feed: no hooks or tools; the feed lives in dashboard/plugin_api.py (routes) and feed.py."""


def register(ctx) -> None:  # noqa: ARG001 - the plugin contributes only its dashboard API
    return None

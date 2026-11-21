"""session-panel (backend half): no hooks or tools. The feed lives in dashboard/ (plugin_api.py routes, feed.py)."""
import sys


def register(ctx) -> None:
    # Unload / disable stops the feed thread (the dashboard module holds it on the shared feed module).
    on_unload = getattr(ctx, 'on_unload', None)
    if callable(on_unload):
        on_unload(stop)


def stop() -> None:
    core = sys.modules.get('_session_panel_feed_core')
    f = getattr(core, 'PROCESS_FEED', None)
    if f is not None:
        f.stopped.set()

"""session-panel (backend half): no hooks or tools. The feed lives in dashboard/ (plugin_api.py routes, feed.py)."""
import sys


def register(ctx) -> None:
    # Unload / disable stops the feed thread (the dashboard module holds it on the shared feed module).
    on_unload = getattr(ctx, 'on_unload', None)
    if callable(on_unload):
        on_unload(stop)
    restart_after_reload()


def restart_after_reload() -> None:
    """A reload (unload, then register again) in a process whose feed ran: resume it now, off this thread (the resume
    waits for the stopped feed's in-flight push). Desktops keep their live window instead of lapsing until their
    next catch-up. A process that never served the feed starts nothing here."""
    core = sys.modules.get('_session_panel_feed_core')
    f, restart = getattr(core, 'PROCESS_FEED', None), getattr(core, 'RESTART', None)
    if f is None or not f.stopped.is_set() or not callable(restart):
        return
    import threading
    threading.Thread(target=restart, name='session-panel-feed-resume', daemon=True).start()


def stop() -> None:
    core = sys.modules.get('_session_panel_feed_core')
    f = getattr(core, 'PROCESS_FEED', None)
    if f is not None:
        f.stopped.set()

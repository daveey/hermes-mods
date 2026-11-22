"""session-panel (backend half): no hooks or tools. The feed lives in dashboard/ (plugin_api.py routes, feed.py)."""
import sys
import types


def register(ctx) -> None:
    # Only the published, authorized host scope enters this adapter. Old cores
    # have no facts attribute and retain the complete legacy implementation.
    facts = getattr(ctx, 'facts', None)
    if facts is not None and getattr(facts, 'v1', None) is not None:
        binding = types.ModuleType('_session_panel_facts_binding')
        binding.v1, binding.scope, binding.handle = facts.v1, facts.scope, facts.handle
        sys.modules['_session_panel_facts_binding'] = binding
        core = sys.modules.get('_session_panel_feed_core')
        install = getattr(core, 'INSTALL_FACTS', None)
        if callable(install):
            install(binding.v1, binding.scope, binding.handle)
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
    loader = sys.modules.get('_session_panel_feed_loader')
    registration = getattr(loader, 'facts_registration', None)
    if registration is not None:
        registration.close()
        loader.facts_registration = None
    if loader is not None:
        loader.facts_mode = 'legacy'
        loader.facts_generation = -1
        for name in ('facts_v1', 'facts_scope', 'facts_handle'):
            loader.__dict__.pop(name, None)
    sys.modules.pop('_session_panel_facts_binding', None)
    core = sys.modules.get('_session_panel_feed_core')
    f = getattr(core, 'PROCESS_FEED', None)
    if f is not None:
        f.stopped.set()

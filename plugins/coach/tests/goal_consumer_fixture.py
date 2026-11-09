import threading
import time
from pathlib import Path
import pytest
import os

@pytest.fixture
def consumer(owner, monkeypatch, request):
    """Execute the real native poller loop with isolated auxiliary drivers.

    Uses the real Kanban notification collector/claim with a test formatter. The sink is a
    test list, not a live Desktop/socket. No credentials or live stores opened.
    """
    SID = os.environ["HERMES_SESSION_KEY"]
    import queue
    import types
    from tui_gateway import session_notifications as notifications
    from tools import async_delegation
    from tools.process_registry import process_registry
    from hermes_cli import kanban_db as kb
    if getattr(request, 'param', '') == 'unreadable':
        from hermes_cli import kanban_db_connect
        def unreadable(*args, **kwargs):
            raise OSError('isolated unreadable notification board')
        monkeypatch.setattr(kanban_db_connect, 'connect', unreadable)
    stop = threading.Event()
    ready = threading.Event()
    notices = []
    session = dict(session_key=SID, profile_home=str(owner[0].parent))
    # Bind the production function's code exactly as method_ctx does at startup.
    g = dict(notifications.__dict__)
    g.update(time=time, _KANBAN_POLL_SECONDS=.01, _BOT_DELIVERY_POLL_SECONDS=5,
             _LOOP_POLL_SECONDS=5, _PARKED_POLL_SECONDS=5,
             _poll_bot_live_delivery_guarded=lambda *a: None,
             _maybe_fire_tui_loop_tick=lambda *a: None,
             _maybe_fire_tui_heartbeat_tick=lambda *a: None,
             _notif_take_parked=lambda *a: None,
             _notif_handle_ready=lambda *a: None)
    from contextlib import closing
    from hermes_state import SessionDB
    from tui_gateway.method_ctx import rebind

    def session_db(current):
        # The delegate-subscription lookup must use the owner's isolated store,
        # never server globals or a live profile's database.
        assert current['profile_home'] == str(owner[0].parent)
        return closing(SessionDB(owner[0].parent / 'state.db'))

    collector_globals = dict(g, Path=Path, _session_db=session_db)
    # Bind the whole helper graph, not a hand-maintained list that drifts when
    # core extracts/adds helpers. Use core's binder to preserve kw-only defaults
    # and closures as well as positional defaults.
    for name, fn in notifications.__dict__.items():
        if isinstance(fn, types.FunctionType) and fn.__module__ == notifications.__name__:
            collector_globals[name] = rebind(fn, collector_globals)
    # Formatter is pure but its helper globals are rebound by the server too.
    collector_globals['_format_kanban_event_text'] = lambda sub, task, ev, slug: (
        f"{sub['chat_id']}:{task.id}:{ev.kind}" if ev.kind == 'completed' else None)
    collector_globals['_KANBAN_NOTIFY_KINDS'] = ('completed',)
    collect = collector_globals['_collect_kanban_notifications']
    def poll(sid, session):
        notices.extend(collect(session))
        ready.set()
    g['_notif_poll_kanban'] = poll
    monkeypatch.setattr(async_delegation, 'maybe_sweep_orphaned_completions', lambda *a: None)
    monkeypatch.setattr(process_registry, 'restore_completions', lambda: None)
    monkeypatch.setattr(process_registry, 'completion_queue', queue.Queue())
    monkeypatch.setattr(kb, 'list_boards', lambda **kw: [dict(slug='default', db_path=str(owner[0]))])
    loop = types.FunctionType(notifications._notification_poller_scoped_loop.__code__, g)
    thread = threading.Thread(target=loop, args=(stop, SID, session), daemon=True)
    g['_notification_pollers'] = [(stop, thread)]
    from tui_gateway.kanban_consumer_health import register
    register(session, stop, thread)
    thread.start()
    try:
        assert ready.wait(3)
        yield session, stop, notices
    finally:
        stop.set()
        thread.join(3)
        assert not thread.is_alive()



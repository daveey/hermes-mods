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
    collector_globals = dict(g, Path=Path)
    collector_globals['_kb_poll_board'] = types.FunctionType(
        notifications._kb_poll_board.__code__, collector_globals,
        argdefs=notifications._kb_poll_board.__defaults__)
    # Core split the per-board claim loop (the formatter call site) out of _kb_poll_board into
    # _kb_poll_board_now: rebind it too, or the real formatter runs instead of the test formatter.
    if hasattr(notifications, '_kb_poll_board_now'):
        collector_globals['_kb_poll_board_now'] = types.FunctionType(
            notifications._kb_poll_board_now.__code__, collector_globals,
            argdefs=notifications._kb_poll_board_now.__defaults__)
    collector_globals['_kb_board_key'] = types.FunctionType(
        notifications._kb_board_key.__code__, collector_globals)
    # Formatter is pure but its helper globals are rebound by the server too.
    collector_globals['_format_kanban_event_text'] = lambda sub, task, ev, slug: (
        f"{sub['chat_id']}:{task.id}:{ev.kind}" if ev.kind == 'completed' else None)
    collector_globals['_KANBAN_NOTIFY_KINDS'] = ('completed',)
    collect = types.FunctionType(notifications._collect_kanban_notifications.__code__, collector_globals,
                                 argdefs=notifications._collect_kanban_notifications.__defaults__)
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



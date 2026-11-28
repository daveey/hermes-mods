import builtins
import dis
import threading
import time
import types
from pathlib import Path
import pytest
import os


def _rebind_globals(g, *sources):
    """Copy all functions bound to these runtime namespaces, including split helpers.

    Core publishes extracted helpers onto server with server globals. Filtering by
    the facade's __module__ misses those helpers and leaks fixture overrides.
    """
    from tui_gateway.method_ctx import rebind
    seen = {}
    for name, fn in list(g.items()):
        body = getattr(fn, '__wrapped__', fn)
        if isinstance(body, types.FunctionType) and any(body.__globals__ is source for source in sources):
            g[name] = rebind(fn, g, seen)
    return g


def runtime_globals(server, module) -> dict:
    """Private runtime namespace, including helpers core extracted from the facade."""
    return _rebind_globals({**vars(server), **vars(module)}, vars(server), vars(module))


def _global_loads(code):
    for ins in dis.get_instructions(code):
        if ins.opname in ('LOAD_GLOBAL', 'LOAD_NAME'):
            yield ins.argval
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _global_loads(const)


def missing_globals(fn, g) -> dict:
    """Bare names that ``fn`` (and every function it reaches through ``g``) loads but neither ``g`` nor builtins
    define, as {function name: sorted names}. Such a name raises NameError only when that line runs, inside the
    poller thread, where it surfaces as a timeout far from its cause."""
    missing, seen, todo = {}, set(), [fn]
    while todo:
        f = todo.pop()
        if id(f) in seen:
            continue
        seen.add(id(f))
        for name in set(_global_loads(f.__code__)):
            if name in g:
                v = g[name]
                v = getattr(v, '__wrapped__', v)  # rebind() wraps @contextmanager helpers
                if isinstance(v, types.FunctionType) and v.__globals__ is g:
                    todo.append(v)
            elif not hasattr(builtins, name):
                missing.setdefault(f.__name__, set()).add(name)
    return {k: sorted(v) for k, v in sorted(missing.items())}


def assert_bound(fn, g, what):
    missing = missing_globals(fn, g)
    assert not missing, (
        f'{what}: rebound core function(s) reference globals the fixture dict lacks: {missing}. '
        'Core now uses a name the fixture does not supply; add it in runtime_globals() or the overrides.')

@pytest.fixture
def consumer(owner, monkeypatch, request):
    """Execute the real native poller loop with isolated auxiliary drivers.

    Uses the real Kanban notification collector/claim with a test formatter. The sink is a
    test list, not a live Desktop/socket. No credentials or live stores opened.
    """
    SID = os.environ["HERMES_SESSION_KEY"]
    import queue
    from tui_gateway import session_notifications as notifications
    from tools import async_delegation
    from tui_gateway import notification_loaded_f
    # These tests exercise goal/claim ownership, not the process-wide 30s
    # discovery scheduler. Keep the real legacy poller and collector on the
    # fixture's .01s cadence, without starting a shared scheduler across tests.
    monkeypatch.setattr(notification_loaded_f, 'enabled', lambda: False)
    from tools.process_registry import process_registry
    from hermes_cli import kanban_db as kb
    if getattr(request, 'param', '') == 'unreadable':
        from hermes_cli import kanban_db_connect
        def unreadable(*args, **kwargs):
            raise OSError('isolated unreadable notification board')
        monkeypatch.setattr(kanban_db_connect, 'connect', unreadable)
        # A successful read-only quiet-board poll may certify health without
        # opening a writable connection. Fail that read too: connect-only failure
        # does not model an unreadable board on current core's idle path.
        from tui_gateway import notification_reader
        monkeypatch.setattr(notification_reader, 'reading', unreadable)
    stop = threading.Event()
    ready = threading.Event()
    notices = []
    session = dict(session_key=SID, profile_home=str(owner[0].parent))
    from contextlib import closing
    from hermes_state import SessionDB
    from tui_gateway import server
    # Bind the production function's code exactly as method_ctx.bind_module does at startup: the bodies run
    # on server.py's globals (sys, logger, _sessions, ...), not on session_notifications' own module dict.
    # Start from a copy of those globals, add the module's own names, and rebind the whole helper graph onto
    # the copy, so the overrides below stay local to this fixture.
    g = runtime_globals(server, notifications)
    g.update(time=time, _KANBAN_POLL_SECONDS=.01, _BOT_DELIVERY_POLL_SECONDS=5,
             _LOOP_POLL_SECONDS=5, _PARKED_POLL_SECONDS=5,
             _poll_bot_live_delivery_guarded=lambda *a: None,
             _maybe_fire_tui_loop_tick=lambda *a: None,
             _maybe_fire_tui_heartbeat_tick=lambda *a: None,
             _notif_take_parked=lambda *a: None,
             _notif_handle_ready=lambda *a: None)

    def session_db(current):
        # The delegate-subscription lookup must use the owner's isolated store,
        # never server globals or a live profile's database.
        assert current['profile_home'] == str(owner[0].parent)
        return closing(SessionDB(owner[0].parent / 'state.db'))

    collector_globals = _rebind_globals(dict(g, Path=Path, _session_db=session_db), g)
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
    assert_bound(loop, g, 'notification poller loop')
    assert_bound(collect, collector_globals, 'kanban notification collector')
    died = []

    def run(*args):
        # A poller that dies before its first poll must fail setup with its own error, not a bare timeout.
        try:
            loop(*args)
        except BaseException as exc:
            died.append(exc)
            ready.set()
            raise

    thread = threading.Thread(target=run, args=(stop, SID, session), name='_notification_poller_scoped_loop',
                              daemon=True)
    g['_notification_pollers'] = [(stop, thread)]
    from tui_gateway.kanban_consumer_health import register
    register(session, stop, thread)
    thread.start()
    try:
        # The first poll opens the isolated board and state store; under a loaded -n 8 run that can take
        # seconds. wait() returns as soon as it lands, so the generous bound costs nothing when healthy.
        assert ready.wait(30), 'native poller made no first Kanban poll within 30 s'
        assert not died, f'native poller thread died before its first poll: {died[0]!r}'
        yield session, stop, notices
    finally:
        stop.set()
        thread.join(3)
        assert not thread.is_alive()



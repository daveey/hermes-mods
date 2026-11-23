"""Additional canonical invalidation and generation reset differentials."""
import ast
from pathlib import Path
import sqlite3

import pytest

from test_reader_batch import graph
from test_snapshot_acquisition import acquisition, acquire
from test_core_readers import api, board, kanban_db


@pytest.mark.parametrize('source_kind', ['local', 'external'])
@pytest.mark.parametrize('change', ['creator', 'subscriber', 'removal', 'expansion', 'none'])
def test_only_exact_captured_scope_can_certify_membership(request, source_kind, change, record_property):
    """One store invariant across real local and replica membership acquisitions."""
    if source_kind == 'local':
        r, cap, m, source, store, conn = request.getfixturevalue('acquisition')
        gateway = 'fixture'
        kanban_db.create_task(conn, title='A', session_id='S0')
        target = kanban_db.create_task(conn, title='B', session_id='S1' if change == 'removal' else 'ancestor')
        connection = None
    else:
        from hermes_cli import kanban_sync_client as client
        r, _, target, _ = request.getfixturevalue('graph')
        cap = r.snapshot_modules()
        m, store = cap.snapshot, cap.snapshot.SnapshotStore()
        gateway = 'local-test'
        source = m.SourceKey(gateway, 'default', 'default', 'hub-links', '/example/external')
        store.add_source(source)
        connection = sqlite3.connect(client.shadow_path('default'))
        conn = connection
        with conn:
            conn.execute("DELETE FROM task_chat_links WHERE task_id=? AND chat_session='S1'", (target,))
            if change == 'removal':
                conn.execute("INSERT INTO task_chat_links(uid,task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) VALUES (?,?,?,?,?,?,0,1)",
                             ('initial-B', target, gateway, 'default', 'S1', 'shepherd'))
    try:
        chats = [m.ChatKey(gateway, 'default', sid) for sid in ('S0', 'S1')]
        store.register([m.Watch(chat, '', (chat.session_id,), 0) for chat in chats])
        def capture(previous, demands):
            hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
            if source_kind == 'local':
                part = r.capture_local(source, previous, demands, hint)
                return part, store.accept_local(part, hint.prior_version)
            part = r.capture_external(demands, {}, {source: previous} if previous else {}, hints={source: hint})[0]
            return part, store.accept_external(part, hint.prior_version)
        _, (initial, _) = capture(None, store.demands(source))
        old = store.pin(chats[1])[1]
        assert not old.missing_scopes
        old_keys = initial.parts[source].scope_keys[store.demand(source, chats[1]).id]
        with conn:
            if source_kind == 'local':
                if change == 'creator':
                    conn.execute('UPDATE tasks SET session_id=? WHERE id=?', ('S1', target))
                elif change == 'subscriber':
                    conn.execute("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui','S1',1)", (target,))
                elif change == 'removal':
                    conn.execute('UPDATE tasks SET session_id=? WHERE id=?', ('ancestor', target))
            elif change in ('creator', 'subscriber', 'expansion'):
                conn.execute("INSERT INTO task_chat_links(uid,task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) VALUES (?,?,?,?,?,?,0,1)",
                             ('new-B', target, gateway, 'default', 'ancestor' if change == 'expansion' else 'S1',
                              'created' if change == 'creator' else 'shepherd'))
            elif change == 'removal':
                conn.execute("DELETE FROM task_chat_links WHERE uid='initial-B'")
        if change == 'expansion':
            store.register([m.Watch(chats[1], '', ('S1', 'ancestor'), 0)])
            owner = gateway if source_kind == 'local' else 'remote-test'
            store.require(chats[1], [m.CardKey(owner, 'default', 'default', target)])
        a = store.demand(source, chats[0])
        part, (partial, admissions) = capture(initial.parts[source], {a.id: a})
        b = store.demand(source, chats[1])
        assert b.id not in part.receipt.coverage.captured_demands
        if change == 'none':
            assert part.receipt.cursor == initial.parts[source].receipt.cursor
        assert b.id not in partial.parts[source].scope_receipts, 'omitted scope was certified without being read'
        assert b.id in store.pending(source)
        if change != 'expansion':  # require/register already admitted the new demand
            assert chats[1] in {admission.chat for admission in admissions}
        assert store.pin(chats[1])[1].missing_scopes
        assert not store.current(old)
        assert old_keys <= partial.parts[source].rows.keys(), 'last-known payload was discarded'
        with pytest.raises(m.CoverageUnavailable):
            m.SnapshotReaders(partial, chats[1]).linked(list(b.lineage))
        record_property('omitted_scope_pending_after_partial', 1)
        record_property('omitted_scope_accept_re_admissions', int(chats[1] in {item.chat for item in admissions}))
        _, (fresh, _) = capture(partial.parts[source], store.demands(source))
        assert not store.pin(chats[1])[1].missing_scopes
        rows = m.SnapshotReaders(fresh, chats[1]).linked(list(b.lineage))
        assert (target in {row['id'] for row in rows}) == (change not in ('removal', 'none'))
    finally:
        if connection is not None:
            connection.close()


def test_scope_certificate_writes_are_sealed_to_accept():
    """Adapters issue capture evidence, never store certificates; lifecycle may only revoke."""
    dashboard = Path(__file__).resolve().parents[1] / 'dashboard'
    writers = []
    for path in dashboard.glob('*.py'):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.keyword) and node.arg == 'scope_receipts':
                    writers.append((path.name, function.name))
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if not isinstance(target, ast.Subscript):
                            continue
                        if isinstance(target.value, ast.Attribute) and target.value.attr == 'scope_receipts':
                            raise AssertionError('direct certificate mutation outside immutable store')
                        if isinstance(target.value, ast.Name) and target.value.id == 'receipts':
                            assert (path.name, function.name) == ('board_snapshot.py', '_accept')
            if path.name == 'board_snapshot.py' and function.name in ('_retire_obsolete', 'forget'):
                # These lifecycle writers may only filter an existing certificate
                # map. They cannot construct a receipt or import adapter coverage.
                definitions = [node.value for node in ast.walk(function)
                               if isinstance(node, ast.Assign) and any(
                                   isinstance(target, ast.Name) and target.id == 'receipts' for target in node.targets)]
                assert len(definitions) == 1
                value = definitions[0]
                assert isinstance(value, ast.DictComp)
                assert isinstance(value.value, ast.Name) and value.value.id == 'v'
                assert value.generators[0].ifs
                iterator = value.generators[0].iter
                assert isinstance(iterator, ast.Call) and isinstance(iterator.func, ast.Attribute)
                assert iterator.func.attr == 'items' and isinstance(iterator.func.value, ast.Attribute)
                assert iterator.func.value.attr == 'scope_receipts'
            if path.name == 'board_snapshot.py' and function.name == 'reset':
                writes = [node.value for node in ast.walk(function)
                          if isinstance(node, ast.keyword) and node.arg == 'scope_receipts']
                assert len(writes) == 1 and isinstance(writes[0], ast.Dict) and not writes[0].keys
    assert set(writers) == {('board_snapshot.py', name) for name in ('_accept', '_retire_obsolete', 'reset', 'forget')}


def test_deep_enrichment_dependency_past_running_detail_stop(acquisition):
    r, cap, m, source, store, conn = acquisition
    blocked = kanban_db.create_task(conn, title='Distant blocked producer')
    with conn:
        conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (blocked,))
    middle = kanban_db.create_task(conn, title='Middle', parents=[blocked])
    running = kanban_db.create_task(conn, title='Running', parents=[middle], initial_status='running')
    child = kanban_db.create_task(conn, title='Dependent', session_id='S', parents=[running])
    with conn:
        conn.execute("UPDATE tasks SET status='ready' WHERE id IN (?,?)", (child, middle))
        conn.execute("UPDATE tasks SET status='running' WHERE id=?", (running,))
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    with conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (blocked,))
    successor, _ = acquire(acquisition, first)
    assert successor.metrics['full'] is False
    fresh, _ = acquire(acquisition)
    key = m.CardKey('fixture', 'default', 'default', child)
    assert first.rows[key].get('blocked_upstream')
    assert successor.rows[key] == fresh.rows[key]
    assert successor.details[key] == fresh.details[key]


def test_partial_capture_cannot_advance_cursor_over_stale_carried_rows(acquisition):
    r, cap, m, source, store, conn = acquisition
    a = kanban_db.create_task(conn, title='A', session_id='A')
    b = kanban_db.create_task(conn, title='B old', session_id='B')
    chats = [m.ChatKey('fixture', 'default', sid) for sid in ('A', 'B')]
    store.register([m.Watch(chat, '', (chat.session_id,), 0) for chat in chats])
    before, _ = acquire(acquisition)
    with conn:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('B new', b))
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    demands = store.demands(source)
    part = r.capture_local(source, before, {store.demand(source, chats[0]).id: demands[store.demand(source, chats[0]).id]}, hint)
    snapshot, _ = store.accept_local(part, hint.prior_version)
    assert store.pin(chats[1])[1].missing_scopes
    current, _ = acquire(acquisition, snapshot.parts[source])
    fresh, _ = acquire(acquisition)
    key = m.CardKey('fixture', 'default', 'default', b)
    assert current.rows[key] == fresh.rows[key]
    assert current.details[key] == fresh.details[key]


@pytest.mark.parametrize('membership', ['creator', 'subscriber'])
@pytest.mark.parametrize('intermediate', [False, True])
def test_partial_capture_revokes_empty_omitted_membership(acquisition, membership, intermediate):
    r, cap, m, source, store, conn = acquisition
    kanban_db.create_task(conn, title='A', session_id='A')
    target = kanban_db.create_task(conn, title='Unlinked', session_id='other')
    chats = [m.ChatKey('fixture', 'default', sid) for sid in ('A', 'B')]
    store.register([m.Watch(chat, '', (chat.session_id,), 0) for chat in chats])
    previous, _ = acquire(acquisition)
    empty_admission = store.pin(chats[1])[1]
    assert not empty_admission.missing_scopes
    demand = store.demand(source, chats[0])
    if intermediate:
        hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
        partial = r.capture_local(source, previous, {demand.id: demand}, hint)
        snapshot, _ = store.accept_local(partial, hint.prior_version)
        previous = snapshot.parts[source]
        assert not store.current(empty_admission)  # omitted even at unchanged observation
        assert store.pin(chats[1])[1].missing_scopes
    with conn:
        if membership == 'creator':
            conn.execute('UPDATE tasks SET session_id=? WHERE id=?', ('B', target))
        else:
            conn.execute("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui','B',1)", (target,))
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    partial = r.capture_local(source, previous, {demand.id: demand}, hint)
    snapshot, admissions = store.accept_local(partial, hint.prior_version)
    if not intermediate:
        assert chats[1] in {item.chat for item in admissions}
    assert store.pin(chats[1])[1].missing_scopes
    assert not store.current(empty_admission)
    successor, final = acquire(acquisition, snapshot.parts[source])
    assert not store.pin(chats[1])[1].missing_scopes
    assert [row['id'] for row in m.SnapshotReaders(final, chats[1]).linked(['B'])] == [target]


def test_terminal_capture_does_not_recertify_old_detail_after_reset(acquisition):
    r, cap, m, source, store, conn = acquisition
    tid = kanban_db.create_task(conn, title='Initially ready', session_id='S')
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    with conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
    store.reset(source)
    _, snapshot = acquire(acquisition, first)
    key = m.CardKey('fixture', 'default', 'default', tid)
    actual = m.SnapshotReaders(snapshot, chat).card_key(key)
    assert actual is None or actual['detail']['task']['status'] == 'done'
    assert key not in snapshot.parts[source].details

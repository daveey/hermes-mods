"""The real readers against a board built with core's own kanban_db: card records, changed cards, stamps, lineage."""
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

kanban_db = pytest.importorskip('hermes_cli.kanban_db')


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    name = '_spf_api_test'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def board(tmp_path):
    path = tmp_path / 'kanban.db'
    from hermes_cli.kanban_db_connect import connect
    conn = connect(path)
    try:
        yield path, conn
    finally:
        conn.close()


def readers_for(api, path, monkeypatch):
    r = api.CoreReaders(state_db=path.parent / 'state.db')
    monkeypatch.setattr(r, 'boards', lambda: [('default', path)])
    return r


def test_card_record_has_what_the_panel_chain_reads(api, board, monkeypatch):
    path, conn = board
    leaf = kanban_db.create_task(conn, title='Leaf work', initial_status='running', session_id='S9')
    waiting = kanban_db.create_task(conn, title='Waits on leaf', parents=[leaf], initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    rec = r.card(waiting, 'default')
    d = rec['detail']
    assert rec['board'] == 'default' and d['slim'] is True and d['source'] == 'live'
    assert d['task']['id'] == waiting and d['task']['title'] == 'Waits on leaf' and d['task']['session_id'] == 'S1'
    assert d['links']['parents'] == [leaf]
    assert d['link_tasks'] == [{'id': leaf, 'title': 'Leaf work', 'status': kanban_db.get_task(conn, leaf).status}]
    assert isinstance(d['events'], list) and len(d['events']) <= api.EVENTS_KEPT
    assert 'wake_check' in d['task']
    assert r.card('t_nope000', 'default') is None
    assert len(api.json.dumps(api.json_safe(rec))) < 20_000, 'slim: a few KB, not the 48 KB full detail'


def test_changed_cards_reports_cards_with_new_events_and_their_chat(api, board, monkeypatch):
    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    changed, cursor = r.changed_cards(None)
    assert changed == {}, 'the first call only sets the cursor'
    stamp = r.stamps()
    b = kanban_db.create_task(conn, title='B', initial_status='running', session_id='S2')
    kanban_db.block_task(conn, a, reason='waiting on a person')
    changed, cursor2 = r.changed_cards(cursor)
    assert changed.get(a) == 'S1' and changed.get(b) == 'S2'
    assert r.changed_cards(cursor2)[0] == {}, 'nothing new since'
    assert r.stamps() != stamp, 'a board write moves the stamps'


def test_semantic_board_changes_include_unevented_writes_and_new_subscribers(api, board, monkeypatch):
    path, conn = board
    from hermes_state import SessionDB
    db = SessionDB(db_path=path.parent / 'state.db')
    try:
        db.create_session('S1', source='desktop')
    finally:
        db.close()
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    changes, cursor = r.board_changes(None)
    assert changes.full
    conn.execute('UPDATE tasks SET title=? WHERE id=?', ('without an event', a))
    conn.commit()
    changes, cursor = r.board_changes(cursor)
    assert not changes.full
    assert changes.sources == {(str(path), 'default'): {a}}
    assert changes.sessions == {'S1'}
    # The new subscriber has no previous bundle ref to the task. A max(event.id)
    # cursor cannot discover this membership-only write.
    columns = {row[1] for row in conn.execute('PRAGMA table_info(kanban_notify_subs)')}
    assert 'chat_id' in columns
    conn.execute("INSERT INTO kanban_notify_subs(task_id, platform, chat_id, created_at) VALUES (?, 'tui', 'S2', 1000)", (a,))
    conn.commit()
    changes, cursor = r.board_changes(cursor)
    assert changes.sources == {(str(path), 'default'): {a}}
    assert {'S1', 'S2'} <= changes.sessions
    changes, _ = r.board_changes(cursor)
    assert not changes.sources and not changes.sessions and not changes.full
    r.release()


def test_semantic_cursor_includes_new_remote_chat_links(api, board, monkeypatch):
    from hermes_cli import kanban_activity_revision as revision
    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='foreign-chat')
    conn.execute('CREATE TABLE IF NOT EXISTS task_chat_links ('
                 'uid TEXT PRIMARY KEY, task_id TEXT, chat_gateway TEXT, chat_profile TEXT, '
                 'chat_session TEXT, kind TEXT, released INTEGER, created_at INTEGER)')
    revision.ensure_revision(conn, revision.LOCAL_TABLES)
    conn.commit()
    r = readers_for(api, path, monkeypatch)
    _, cursor = r.board_changes(None)
    conn.execute("INSERT INTO task_chat_links(uid,task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) "
                 "VALUES ('link-one',?,'gateway','default','new-origin-chat','shepherd',0,1000)", (a,))
    conn.commit()
    changes, _ = r.board_changes(cursor)
    assert not changes.full
    assert 'new-origin-chat' in changes.sessions


def test_semantic_cursor_idle_has_no_db_opens(api, board, monkeypatch):
    path, conn = board
    kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    _, cursor = r.board_changes(None)
    def forbidden(*args, **kwargs):
        raise AssertionError('unchanged source opened a database')
    monkeypatch.setattr(api.sqlite3, 'connect', forbidden)
    for _ in range(60):
        changes, again = r.board_changes(cursor)
        assert again == cursor
        assert not changes.sources and not changes.full and not changes.sessions


def test_semantic_cursor_does_not_adopt_failed_capture(api, board, monkeypatch):
    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    _, cursor = r.board_changes(None)
    before = dict(cursor)
    conn.execute('UPDATE tasks SET title=? WHERE id=?', ('changed', a))
    conn.commit()
    from hermes_cli import kanban_task_change as ktc
    canonical = ktc.changed_since
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('busy source')
    monkeypatch.setattr(ktc, 'changed_since', fail)
    with pytest.raises(sqlite3.OperationalError, match='busy source'):
        r.board_changes(cursor)
    assert cursor == before
    monkeypatch.setattr(ktc, 'changed_since', canonical)
    changes, _ = r.board_changes(cursor)
    assert a in changes.sources[(str(path), 'default')]


def test_semantic_cursor_source_removal_invalidates_old_membership(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    _, cursor = r.board_changes(None)
    monkeypatch.setattr(r, 'files', lambda: [])
    changes, cursor = r.board_changes(cursor)
    assert changes.full and cursor == {}


def test_semantic_capture_owns_and_closes_one_query_only_connection(api, board, monkeypatch):
    from hermes_cli import kanban_task_change as ktc
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    seen = []
    canonical = ktc.changed_since
    def capture(c, cursor):
        seen.append(c)
        assert c.execute('PRAGMA query_only').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            c.execute("UPDATE tasks SET title='not permitted'")
        return canonical(c, cursor)
    monkeypatch.setattr(ktc, 'changed_since', capture)
    r.board_changes(None)
    assert len(seen) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        seen[0].execute('SELECT 1')


def test_semantic_cursor_regressed_revision_invalidates_all(api, board, monkeypatch):
    path, conn = board
    kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    _, cursor = r.board_changes(None)
    conn.execute('UPDATE kanban_activity_revision SET revision=0')
    conn.execute('UPDATE kanban_task_change SET rev=0')
    conn.commit()
    changes, _ = r.board_changes(cursor)
    assert changes.full


def test_semantic_cursor_coverage_loss_is_full(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    _, cursor = r.board_changes(None)
    conn.execute('DROP TRIGGER kanban_activity_rev_tasks_update')
    conn.commit()
    changes, cursor = r.board_changes(cursor)
    assert changes.full
    assert cursor[str(path)][1] is None


def test_semantic_invalidations_keep_board_coordinates_and_old_refs(api):
    changes = api.BoardChanges(sources={('source.db', 'other-board'): {'t_same0001'}}, sessions={'S-new'})
    old = {'lineage': {'S-old'}, 'bundle': {
        'tasks': [{'id': 't_same0001', 'board': 'default'}], 'done': [], 'cards': {}}}
    assert not changes.affects(old), 'same task id on another board is not this input'
    old['bundle']['tasks'][0]['board'] = 'other-board'
    assert changes.affects(old), 'removed creator/subscriber still has an old scoped ref'
    old['bundle']['tasks'] = []
    old['lineage'] = {'S-new'}
    assert changes.affects(old), 'new member did not have a previous card ref'


def test_lineage_walks_parent_sessions(api, tmp_path, monkeypatch):
    db = tmp_path / 'state.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT)')
    c.executemany('INSERT INTO sessions VALUES (?, ?)', [('S3', 'S2'), ('S2', 'S1'), ('S1', None), ('L1', 'L2'), ('L2', 'L1')])
    c.commit()
    r = api.CoreReaders(state_db=db)
    assert r.lineage('S3') == ['S3', 'S2', 'S1']
    assert r.lineage('L1') == ['L1', 'L2'], 'a cycle ends the walk'
    assert r.lineage('unknown') == ['unknown']


@pytest.mark.parametrize('raw, expected', [('bad-json', {}), ('null', {}), ('[]', {}), ('{"kind":"done"}', {'kind': 'done'})])
def test_cheap_decision_inputs_match_outcome_normalization_without_board_reads(api, tmp_path, monkeypatch, raw, expected):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE sessions(id TEXT, outcome_json TEXT)')
        c.execute('INSERT INTO sessions VALUES (?,?)', ('S1', raw))
    r = api.CoreReaders(state_db=path)
    calls = []
    def canonical(session, lineage, *, reconcile):
        calls.append((session, lineage, reconcile))
        return []
    monkeypatch.setattr(r, '_user_step_requests', canonical)
    assert r.decision_inputs('S1', ['S1']) == {'outcome_json': expected, 'requests': []}
    assert calls == [('S1', ['S1'], False)]
    # A deleted recent chat is a known absent row, not an unavailable database.
    # Without its own stored row no request owner can be established.
    assert r.decision_inputs('foreign', ['foreign']) == {'outcome_json': {}, 'requests': []}
    assert calls == [('S1', ['S1'], False)], 'never infer an owner for the deleted session'
    r.release()


def test_absent_recent_session_refreshes_without_poisoning_freshness(api, tmp_path, monkeypatch):
    from test_feed import make
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE sessions(id TEXT, outcome_json TEXT)')
    r = api.CoreReaders(state_db=path)
    def forbidden(*a, **kw):
        raise AssertionError('absent stored row cannot establish a request owner')
    monkeypatch.setattr(r, '_user_step_requests', forbidden)
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda *a: {'outcome_json': {'kind': 'done'}, 'requests': []}
    f.changes(['S1'])
    assert f.refresh_decisions(readers=r) == 1
    assert not f._decision_failures and 'S1' in pushed[-1]['fresh']
    header = f.bundles['S1']['bundle']['model']['header']
    assert header['state'] == 'waiting' and header['source'] == 'derived'
    assert header['status'] is None
    r.release()


def test_absent_recent_session_through_real_user_steps_does_not_fail_refresh(api, tmp_path):
    from hermes_state import SessionDB
    from test_feed import make
    db = SessionDB(db_path=tmp_path / 'state.db')
    r = api.CoreReaders(state_db=tmp_path / 'state.db')
    try:
        # Exercise the genuine user-steps guard, not a patched request adapter.
        with pytest.raises(Exception, match='Unknown stored session on this profile'):
            r._user_step_requests('deleted-recent', ['deleted-recent'], reconcile=False)
        f, readers, pushed, clock = make()
        f.changes(['deleted-recent'])
        assert f.refresh_decisions(readers=r) == 1
        assert 'deleted-recent' not in f._decision_failures
        assert 'deleted-recent' in pushed[-1]['fresh']
        assert f.bundles['deleted-recent']['bundle']['requests'] == []
        assert f.bundles['deleted-recent']['bundle']['outcome_json'] == {}
    finally:
        r.release()
        db.close()


def test_deleted_session_with_surviving_request_agrees_across_full_and_fast_reads(api, tmp_path, monkeypatch):
    import uuid
    from hermes_state import SessionDB
    from test_feed import feedmod

    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('S2', 'desktop')
    path = Path(__file__).resolve().parents[2] / 'user-steps' / 'core.py'
    spec = importlib.util.spec_from_file_location('_deleted_request_core', path)
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core
    spec.loader.exec_module(core)
    owner = core.server_owner('S2')
    store = core.ActionStore()
    store.create(owner, dict(
        schemaVersion=2, owner=dict(owner), requestId=str(uuid.uuid4()), revision=1,
        kind='approval', title='Approve the synthetic change',
        scope=dict(markdown='Change example.py; run tests.', complete=True, sourceRefs=[]),
        limits=[], exclusions=[], outcomes=dict(approve='Authorize this scope only',
        revise='Keep work blocked', decline='Cancel'),
        choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')],
        aliases=[], state='open'))
    r = api.CoreReaders(state_db=tmp_path / 'state.db')
    monkeypatch.setattr(r, 'boards', lambda: [])
    now = [1000.0]
    pushed = []
    f = feedmod.Feed(r, emit=pushed.append, clock=lambda: now[0])
    try:
        f.changes(['S2'])
        assert len(f.bundles['S2']['bundle']['requests']) == 1
        f.refresh_decisions()
        assert db.delete_session('S2')
        with sqlite3.connect(store.path) as c:
            assert c.execute('SELECT count(*) FROM requests').fetchone()[0] == 1
        f.tick()
        now[0] += 1
        f.tick()
        for _ in range(3):
            now[0] += 301
            f.watch(['S2'])
            f.tick()
            bundle = f.bundles['S2']['bundle']
            assert bundle['requests'] == [], 'full sweep must not resurrect a deleted chat ask'
            assert bundle['model']['sections']['needs_you'] == []
            revision = f.rev
            now[0] += 1
            f.refresh_decisions()
            assert f.bundles['S2']['bundle']['requests'] == []
            assert f.rev == revision, 'the fast lane must agree, without a second revision'
            assert not f._decision_failures
            assert not f.bundles['S2'].get('source_stale')
    finally:
        r.release()
        db.close()


def test_unavailable_decision_sources_raise_instead_of_vouching(api, tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    r = api.CoreReaders(state_db=path)
    with pytest.raises(RuntimeError, match='session outcome unavailable'):
        r.decision_inputs('S1', ['S1'])
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE sessions(id TEXT, outcome_json TEXT)')
        c.execute('INSERT INTO sessions VALUES (?,?)', ('S1', '{}'))
    def broken(*args, **kwargs):
        raise RuntimeError('canonical request store unreadable')
    monkeypatch.setattr(r, '_user_step_requests', broken)
    with pytest.raises(RuntimeError, match='canonical request store unreadable'):
        r.decision_inputs('S1', ['S1'])
    with sqlite3.connect(path) as c:
        c.execute('DROP TABLE sessions')
    with pytest.raises(sqlite3.Error):
        r.decision_inputs('S1', ['S1'])
    r.release()


def test_push_payload_is_json_safe_and_bounded(api):
    body = api.push_payload({'epoch': 'e', 'prev': 1, 'rev': 2, 'bundles': {'S1': {'x': float('nan'), 'title': 'NaN Infinity'}}})
    assert body['bundles']['S1'] == {'x': None, 'title': 'NaN Infinity'}, 'text is untouched'
    big = api.push_payload({'epoch': 'e', 'prev': 1, 'rev': 2, 'bundles': {'S1': {'blob': 'x' * 2000}, 'S2': {}}}, max_bytes=500)
    assert big['bundles'] == {} and big['sessions'] == ['S1', 'S2'], 'too big: names only, clients fetch them'


def test_heartbeats_change_nothing_in_a_card_record(api, board, monkeypatch):
    from types import SimpleNamespace
    from hermes_cli import kanban_queue_position as kqp

    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    # Cross a second boundary deterministically, without changing unrelated core clocks.
    clock = [2_000_000_000]
    monkeypatch.setattr(kqp, 'time', SimpleNamespace(time=lambda: clock[0]))
    r = readers_for(api, path, monkeypatch)
    before = r.card(a, 'default')
    clock[0] += 2
    conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'heartbeat', '{}', 1)", (a,))
    conn.execute('UPDATE tasks SET last_heartbeat_at = 123 WHERE id = ?', (a,))
    conn.commit()
    after = r.card(a, 'default')
    def flat(d, pre=''):
        out = {}
        for k, v in (d.items() if isinstance(d, dict) else enumerate(d)):
            out.update(flat(v, f'{pre}.{k}') if isinstance(v, (dict, list)) else {f'{pre}.{k}': v})
        return out
    fb, fa = flat(before), flat(after)
    diff = {k: (fb.get(k), fa.get(k)) for k in set(fb) | set(fa) if fb.get(k) != fa.get(k)}
    assert diff == {}, f'a heartbeat alone never re-pushes a bundle: {diff}'
    assert 'ready_since' in after['detail']['task']


def test_card_omits_queue_scoring_by_default(api, board, monkeypatch):
    from hermes_cli import kanban_queue_position as kqp
    monkeypatch.delenv('HERMES_PANEL_SKIP_QUEUE_POSITION', raising=False)
    monkeypatch.setattr(kqp, 'detail_fields', lambda *_: pytest.fail('whole-board queue scoring invoked'))
    path, conn = board
    task = kanban_db.create_task(conn, title='No scoring', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    detail = r.card(task, 'default')['detail']
    assert 'queue' not in detail
    r.release()


def test_card_keeps_queue_changes_and_recorded_evidence(api, board, monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_SKIP_QUEUE_POSITION', '0')
    from hermes_cli import kanban_queue_position as kqp

    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    queue = {'lane': 'local', 'ahead': 2, 'observed_at': 100,
             'skip': {'reason': 'capacity', 'observed_at': 90}}
    monkeypatch.setattr(kqp, 'detail_fields', lambda *_: {'queue': queue, 'queue_note': 'waiting'})
    r = readers_for(api, path, monkeypatch)
    before = r.card(a, 'default')['detail']
    assert before['queue'] == {'lane': 'local', 'ahead': 2,
                               'skip': {'reason': 'capacity', 'observed_at': 90}}
    assert queue['observed_at'] == 100, 'do not mutate the core snapshot'
    queue['ahead'] = 1
    after = r.card(a, 'default')['detail']
    assert after['queue']['ahead'] == 1
    assert after['queue_note'] == 'waiting'
    assert before['queue']['ahead'] == 2


def test_each_thread_reads_on_its_own_connection_and_a_replaced_file_is_reopened(api, board, monkeypatch, tmp_path):
    import threading
    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    main = r.conn(path)
    other = []

    def in_thread():
        other.append(r.conn(path))
        r.release()  # a request thread closes its own when done
    th = threading.Thread(target=in_thread)
    th.start(); th.join()
    assert other[0] is not main
    assert r.card(a, 'default') is not None
    # Replace the board file with another one (another inode), the way a swap is done: every connection to the old
    # file closed (its -wal / -shm gone with the last close), then the new file moved in. The reader follows it.
    from hermes_cli.kanban_db_connect import connect
    fresh = tmp_path / 'fresh.db'
    c2 = connect(fresh)
    b = kanban_db.create_task(c2, title='B', initial_status='running', session_id='S2')
    c2.close(); conn.close()
    assert r.conn(path) is main, 'still holding the old file'
    for suffix in ('-wal', '-shm'):
        for f in (f'{path}{suffix}', f'{fresh}{suffix}'):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
    os.replace(fresh, path)
    assert r.card(b, 'default') is not None, 'reads the new file'
    assert r.card(a, 'default') is None


def test_heartbeat_events_are_not_card_changes_and_rows_drop_last_heartbeat(api, board, monkeypatch):
    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    _, cursor = r.changed_cards(None)
    conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'heartbeat', '{}', 1)", (a,))
    conn.commit()
    changed, cursor = r.changed_cards(cursor)
    assert changed == {}, 'a heartbeat is not a change'
    assert api._calm([{'id': a, 'last_heartbeat_at': 5, 'status': 'running'}]) == [{'id': a, 'status': 'running'}]


def test_release_closes_this_threads_connections(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    c = r.conn(path)
    r.release()
    import sqlite3 as _sq
    with pytest.raises(_sq.ProgrammingError):
        c.execute('select 1')
    assert r.conn(path) is not None, 'reopened on demand'



def test_a_board_whose_wal_files_were_removed_by_the_last_writer_still_opens(api, board, monkeypatch):
    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    conn.close()  # the last writer's checkpoint removes -wal / -shm
    for suffix in ('-wal', '-shm'):
        try:
            os.remove(f'{path}{suffix}')
        except FileNotFoundError:
            pass
    r = readers_for(api, path, monkeypatch)
    assert r.card(a, 'default') is not None
    import sqlite3 as _sq
    with pytest.raises(_sq.OperationalError):
        r.conn(path).execute('DELETE FROM tasks')
    assert not (path.parent / 'missing.db').exists()
    assert r.conn(path.parent / 'missing.db') is None, 'a missing file is never created'
    assert not (path.parent / 'missing.db').exists()


def test_batched_panel_stamps_equal_the_per_chat_stamp_and_leave_no_connection_after_release(api, tmp_path, monkeypatch):
    import uuid
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / 'state.db')
    for s in ('S1', 'S2', 'S3'):
        db.create_session(s, 'desktop')
    path = Path(__file__).resolve().parents[2] / 'user-steps' / 'core.py'
    spec = importlib.util.spec_from_file_location('_batched_stamp_core', path)
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core
    spec.loader.exec_module(core)
    store = core.ActionStore()
    for s in ('S2', 'S2', 'S3'):
        owner = core.server_owner(s)
        store.create(owner, dict(
            schemaVersion=2, owner=dict(owner), requestId=str(uuid.uuid4()), revision=1,
            kind='approval', title='Approve the synthetic change',
            scope=dict(markdown='Change example.py; run tests.', complete=True, sourceRefs=[]),
            limits=[], exclusions=[], outcomes=dict(approve='Authorize this scope only',
            revise='Keep work blocked', decline='Cancel'),
            choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')],
            aliases=[], state='open'))
    r = api.CoreReaders(state_db=tmp_path / 'state.db')
    ids = ['S1', 'S2', 'S3', 'never-stored']
    single = {s: r.panel_stamp(s) for s in ids}
    assert len(single['S2'][1]) == 2 and single['S1'][1] == () and single['never-stored'] == (None, (), None)
    assert r.panel_stamps(ids) == single
    assert r.panel_stamps([]) == {}
    before = r.panel_files_stamp()
    assert before == r.panel_files_stamp(), 'stat only: stable while nothing is written'
    owner = core.server_owner('S1')
    store.create(owner, dict(
        schemaVersion=2, owner=dict(owner), requestId=str(uuid.uuid4()), revision=1,
        kind='approval', title='Another', scope=dict(markdown='x', complete=True, sourceRefs=[]),
        limits=[], exclusions=[], outcomes=dict(approve='a', revise='b', decline='c'),
        choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')],
        aliases=[], state='open'))
    assert r.panel_files_stamp() != before and r.decision_store_stamp() != before[1]
    assert r.panel_stamps(ids) == {s: r.panel_stamp(s) for s in ids} != single
    r.release()
    assert r._local.conns == {}


def test_card_detail_reads_only_the_newest_events_and_latest_run(api, board, monkeypatch):
    """Same events and run as filtering every event in Python, read through one LIMITed query. [daveey]"""
    import dataclasses
    path, conn = board
    cid = kanban_db.create_task(conn, title='Busy card', initial_status='running', session_id='S1')
    with conn:
        for i in range(300):
            kind = 'heartbeat' if i % 3 else ('commented' if i % 2 else 'blocked')
            conn.execute('INSERT INTO task_events(task_id, kind, payload, created_at) VALUES (?,?,?,?)',
                         (cid, kind, None, 1_000 + i // 2))
        for i in range(3):
            conn.execute('INSERT INTO task_runs(task_id, status, started_at) VALUES (?,?,?)', (cid, 'done', 5 + i))
    expected_events = [dataclasses.asdict(e) for e in kanban_db.list_events(conn, cid)
                       if e.kind != 'heartbeat'][-api.EVENTS_KEPT:]
    expected_runs = [dataclasses.asdict(r) for r in kanban_db.list_runs(conn, cid)][-1:]
    for run in expected_runs:
        run.pop('last_heartbeat_at', None)
    r = readers_for(api, path, monkeypatch)
    sql = []
    real_conn = r.conn

    def traced(p):
        c = real_conn(p)
        if c is not None:
            c.set_trace_callback(sql.append)
        return c

    monkeypatch.setattr(r, 'conn', traced)
    d = r.card(cid, 'default')['detail']
    assert d['events'] == expected_events and len(d['events']) == api.EVENTS_KEPT
    assert d['runs'] == expected_runs
    # The slim display tail remains bounded; optional core diagnostics also read
    # source-kind-filtered per-task history, whose volume bound is not ours.
    event_reads = [s for s in sql if 'FROM task_events WHERE task_id' in s and "kind != 'heartbeat'" in s]
    assert event_reads and all('LIMIT' in s for s in event_reads), event_reads


def test_card_detail_counts_failed_runs_for_the_panel(api, board, monkeypatch):
    """`failed_runs` counts every failed run of the card, not reclaims or successes, and 0 for a clean card. [daveey]"""
    path, conn = board
    cid = kanban_db.create_task(conn, title='Flaky card', initial_status='running', session_id='S1')
    clean = kanban_db.create_task(conn, title='Clean card', initial_status='running', session_id='S1')
    other = kanban_db.create_task(conn, title='Other card', initial_status='running', session_id='S1')
    with conn:
        for outcome in ('crashed', 'timed_out', 'spawn_failed', 'oom_killed', 'reclaimed', 'completed', 'rate_limited', None):
            conn.execute('INSERT INTO task_runs(task_id, status, outcome, started_at) VALUES (?,?,?,?)', (cid, 'done', outcome, 5))
        conn.execute('INSERT INTO task_runs(task_id, status, outcome, started_at) VALUES (?,?,?,?)', (other, 'done', 'crashed', 5))
    r = readers_for(api, path, monkeypatch)
    assert r.card(cid, 'default')['detail']['task']['failed_runs'] == 4
    assert r.card(clean, 'default')['detail']['task']['failed_runs'] == 0


def test_done_stats_remapping_has_linear_id_reads(api, tmp_path, monkeypatch):
    """Hydration must not search the full chat's rows again for each result."""
    reads = []

    class Row(dict):
        def get(self, key, *default):
            if key == 'id':
                reads.append(key)
            return super().get(key, *default)

    path = tmp_path / 'stats.db'
    path.touch()
    reader = api.CoreReaders(state_db=tmp_path / 'state.db')
    monkeypatch.setattr(reader, 'boards', lambda: [('default', path)])
    monkeypatch.setattr(reader, 'conn', lambda _: object())
    feed = api._load_feed()

    def stats(rows, conn):
        return [{**row, 'run_stats': {'runs': 1}} for row in rows]

    monkeypatch.setattr(feed, 'with_done_run_stats', stats)
    rows = [Row(id=f't_{i}', status='done', board='default') for i in range(400)]
    result = reader._with_run_stats(rows)
    assert len(result) == 400 and all(row['run_stats'] == {'runs': 1} for row in result)
    assert all('run_stats' not in row for row in rows), 'core memo rows must not be mutated'
    assert len(reads) <= 4 * len(rows), 'a per-result linear search makes the pass quadratic'


@pytest.mark.parametrize('key', ['t_same', None, ['legacy-unhashable']])
def test_done_stats_duplicate_ids_preserve_first_match(api, tmp_path, monkeypatch, key):
    path = tmp_path / 'stats.db'
    path.touch()
    reader = api.CoreReaders(state_db=tmp_path / 'state.db')
    monkeypatch.setattr(reader, 'boards', lambda: [('default', path), ('other', path)])
    monkeypatch.setattr(reader, 'conn', lambda _: object())

    def stats(rows, conn):
        return [{**row, 'run_stats': {'marker': row['marker']}} for row in rows]

    monkeypatch.setattr(api._load_feed(), 'with_done_run_stats', stats)
    rows = [
        {'id': key, 'status': 'done', 'marker': 1},
        {'id': key, 'status': 'archived', 'marker': 2},
        {'id': key, 'status': 'done', 'board': 'other', 'marker': 3},
        {'id': key, 'status': 'running', 'marker': 4},
        {'id': key, 'status': 'done', 'source': 'origin-history', 'marker': 5},
    ]
    got = reader._with_run_stats(rows)
    assert got[0]['run_stats'] == {'marker': 2}, 'first original gets last hydrated duplicate'
    assert got[1] is rows[1] and 'run_stats' not in got[1]
    assert got[2]['run_stats'] == {'marker': 3}, 'board scopes must stay separate'
    assert got[3] is rows[3] and got[4] is rows[4]
    assert all('run_stats' not in row for row in rows)

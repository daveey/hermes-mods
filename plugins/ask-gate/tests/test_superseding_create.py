import json
import sqlite3
from pathlib import Path

import pytest

from test_superseding_work import _board, _home, FILLER

FIX = json.loads((Path(__file__).parent / 'fixtures/superseding_create_incident.json').read_text())


def seed(gate):
    _board(_home(gate) / 'kanban.db', FILLER + [dict(r, status='todo') for r in FIX[:2]])
    return {'title': FIX[2]['title'], 'body': FIX[2]['body']}


def test_real_design_change_requires_disposition(gate):
    args = seed(gate)
    held = gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1')
    assert held and 'superseding_create' in held['message']
    for old in FIX[:2]:
        assert old['id'] in held['message']
    # Identical retries remain refused.
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1')


def test_explicit_independence_releases_real_pair(gate):
    args = seed(gate)
    args['body'] += '\nSupersedes:\n' + '\n'.join(
        r['id'] + ': independent: This card only measures latency and freshness; keep the old visual acceptance.'
        for r in FIX[:2])
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1') is None


def test_same_subsystem_freshness_work_can_remain_independent(gate):
    seed(gate)
    args = {
        'title': 'Measure sidebar latency and freshness',
        'body': 'Measure sidebar GOVERNORS row update latency, stale data age and refresh jitter. '
                'This is instrumentation, not a replacement visual design.\nSupersedes:\n'
                + '\n'.join(r['id'] + ': independent: Retain the existing visual acceptance; this adds freshness metrics only.'
                            for r in FIX[:2]),
    }
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1') is None


def test_unrelated_create_untouched(gate):
    seed(gate)
    assert gate.pre_tool_call(tool_name='kanban_create', args={
        'title': 'Sort recipes', 'body': 'Group lentil recipes by ingredient and spice.'}, session_id='s1') is None


@pytest.mark.parametrize('declaration', ['independent', 'archive: reason\n' + FIX[0]['id'] + ': amend', 'bogus'])
def test_malformed_or_duplicate_disposition_refused(gate, declaration):
    args = seed(gate)
    args['body'] += '\nSupersedes:\n' + FIX[0]['id'] + ': ' + declaration
    assert 'superseding_create' in gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1')['message']


def test_all_matches_not_capped_at_six(gate):
    rows = [dict(FIX[0], id='t_%016x' % n, status='todo') for n in range(12)]
    _board(_home(gate) / 'kanban.db', rows)
    held = gate.pre_tool_call(tool_name='kanban_create', args={'title': FIX[2]['title'], 'body': FIX[2]['body']}, session_id='s1')
    assert all(r['id'] in held['message'] for r in rows)


def test_parents_and_created_this_turn_excluded(gate):
    args = seed(gate)
    args['parents'] = [FIX[0]['id']]
    gate.post_tool_call(tool_name='kanban_create', args={'title': 'Earlier child'},
                        result={'ok': True, 'task_id': FIX[1]['id']}, session_id='s1')
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1') is None
    gate.LOG.reset('s1')
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1')


@pytest.mark.parametrize('status', ['running', 'done', 'archived'])
def test_terminal_and_running_candidates_untouched(gate, status):
    _board(_home(gate) / 'kanban.db', [dict(r, status=status) for r in FIX[:2]])
    assert gate.pre_tool_call(tool_name='kanban_create', args={'title': FIX[2]['title'], 'body': FIX[2]['body']}, session_id='s1') is None


def test_named_removal_keeps_original_threshold(gate, monkeypatch):
    seed(gate)
    monkeypatch.setattr(gate.superseding_create, 'MIN_COVERAGE', .99)
    held = gate.pre_tool_call(tool_name='kanban_create', args={'title': FIX[2]['title'], 'body': FIX[2]['body']}, session_id='s1')
    assert all(r['id'] in held['message'] for r in FIX[:2])
    assert gate.superseding_create.removals('Do not delete GOVERNORS section.') == []
    assert gate.superseding_create.removals('Never remove `api.route`.') == []
    assert gate.superseding_create.removals('Delete the GOVERNORS section.') == ['GOVERNORS']
    assert not gate.superseding_create.removes_named('Keep api.other', ['api.route'])


def test_amend_requires_actual_successful_comment_readback(gate):
    args = seed(gate)
    path = _home(gate) / 'kanban.db'
    with sqlite3.connect(path) as c:
        c.execute('create table task_comments(id integer, task_id text, body text)')
        c.execute('insert into task_comments values(1,?,?)', (FIX[0]['id'], 'Acceptance change: remove the obsolete GOVERNORS list acceptance.'))
    args['body'] += '\nSupersedes:\n' + FIX[0]['id'] + ': amend\n' + FIX[1]['id'] + ': independent: Separate completion dashboard.'
    assert 'successful kanban_comment' in gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1')['message']
    gate.post_tool_call(tool_name='kanban_comment', args={'task_id': FIX[0]['id']},
                        result={'ok': True, 'task_id': FIX[0]['id'], 'comment_id': 1}, session_id='s1')
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1') is None


def test_config_off_and_default_on(gate, monkeypatch):
    cfg = pytest.importorskip('hermes_cli.config')
    args = seed(gate)
    monkeypatch.setattr(cfg, 'load_config_readonly', lambda: {'ask_gate': {'superseding_create': False}})
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1') is None
    monkeypatch.setattr(cfg, 'load_config_readonly', lambda: {})
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1')


def test_inventory_failure_closed(gate, monkeypatch):
    def broken(*args):
        raise OSError('inventory unavailable')
    monkeypatch.setattr(gate.superseding_create, 'inventory', broken)
    held = gate.pre_tool_call(tool_name='kanban_create', args={'title': 'Something'}, session_id='s1')
    assert 'could not be verified' in held['message']


def test_archive_only_after_exact_successful_create(gate, monkeypatch):
    args = seed(gate)
    args['body'] += '\nSupersedes:\n' + '\n'.join(r['id'] + ': archive' for r in FIX[:2])
    calls = []
    def archive(path, tid, **kw):
        calls.append((tid, kw))
        return None
    monkeypatch.setattr(gate.superseding_create, 'archive_candidate', archive)
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1') is None
    assert all(not kw for _, kw in calls)
    calls.clear()
    gate.post_tool_call(tool_name='kanban_create', args=args, result={'ok': False, 'error': 'refused'}, session_id='s1')
    assert not calls
    assert gate.pre_tool_call(tool_name='kanban_create', args=args, session_id='s1') is None
    calls.clear()
    gate.post_tool_call(tool_name='kanban_create', args=args, result={'ok': True, 'task_id': 't_abcdef1234567890'}, session_id='s1')
    assert len(calls) == 2 and all(kw['new_id'] == 't_abcdef1234567890' for _, kw in calls)


def test_core_archive_atomic_pointer_and_claim_race(gate, monkeypatch):
    import tempfile
    from contextlib import contextmanager
    kb = pytest.importorskip('hermes_cli.kanban_db')
    kbc = pytest.importorskip('hermes_cli.kanban_db_connect')
    kt = pytest.importorskip('tools.kanban_tools')
    ktb = pytest.importorskip('tools.kanban_tools_board')
    from agent.delegation_context import kanban_path_is_fenced
    with tempfile.TemporaryDirectory(prefix='supersession-fixture-', dir=Path.home()) as directory:
        home = Path(directory)
        path = home / 'kanban.db'
        monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
        monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
        monkeypatch.setenv('HERMES_HOME', str(home))
        assert not kanban_path_is_fenced(path)
        conn = kbc.connect(path)
        try:
            old = kb.create_task(conn, title='Legacy sidebar', body='Show GOVERNORS section', assignee='default', created_by='fixture')
            new = kb.create_task(conn, title='Replacement sidebar', body='Delete GOVERNORS section', assignee='default', created_by='fixture')
            assert conn.execute('pragma database_list').fetchone()[2] == str(path)
            # Use the real core DB/archive; only board selection is fixture-owned.
            @contextmanager
            def board(_):
                yield kb, conn
            monkeypatch.setattr(ktb, 'tool_board', board)
            # Descendant admission remains real against this verified scratch home.
            assert gate.superseding_create.archive_candidate(path, old, new_id=new) is None
            assert conn.execute('select status from tasks where id=?', (old,)).fetchone()[0] == 'archived'
            assert new in conn.execute('select body from task_comments where task_id=?', (old,)).fetchone()[0]
            racing = kb.create_task(conn, title='Claim race sidebar', body='Show GOVERNORS section', assignee='default', created_by='fixture')
            real = kb.archive_task
            def race(c, tid, **kw):
                c.execute("update tasks set status='running' where id=?", (tid,))
                return real(c, tid, **kw)
            monkeypatch.setattr(kb, 'archive_task', race)
            refusal = gate.superseding_create.archive_candidate(path, racing, new_id=new)
            assert refusal and 'precondition' in refusal
            assert conn.execute('select status from tasks where id=?', (racing,)).fetchone()[0] == 'running'
            assert conn.execute('select count(*) from task_comments where task_id=?', (racing,)).fetchone()[0] == 0
        finally:
            conn.close()

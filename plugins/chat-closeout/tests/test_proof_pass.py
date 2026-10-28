"""Sidebar waiting pass cost: one pass reuses its reads (one state.db connection, one read-only SessionDB, one
compression-tip lookup per chat) and proves exactly what per-proof opens proved."""
import sqlite3
import sys
import types

import pytest
from test_closeout import plugin, core

hermes_state = pytest.importorskip('hermes_state')


@pytest.fixture
def world(tmp_path, monkeypatch):
    """state.db with a compressed chat (root -> tip) whose tip answered t_acked's notice; counts every open."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    db = hermes_state.SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('root', source='cli')
    db.end_session('root', 'compression')
    db.create_session('tip', source='cli', parent_session_id='root')
    db.create_session('plain', source='cli')
    db.close()
    with sqlite3.connect(tmp_path / 'state.db') as conn:
        cols = {r[1] for r in conn.execute('PRAGMA table_info(messages)')}
        assert {'display_kind', 'finish_reason', 'tool_calls', 'active'} <= cols
        for sid, role, text, at, finish in (
                ('tip', 'user', '✔ [default] @default Kanban t_acked done — local', 101, None),
                ('tip', 'assistant', 'Result checked.', 102, 'stop')):
            conn.execute('INSERT INTO messages (session_id, role, content, timestamp, finish_reason, active) '
                         'VALUES (?,?,?,?,?,1)', (sid, role, text, at, finish))
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [tmp_path / 'state.db'])

    real_db, real_connect = hermes_state.SessionDB, core.ro_connect
    opens = {'sessiondb': [], 'connect': 0}

    class Spy(real_db):
        def __init__(self, db_path=None, read_only=False):
            super().__init__(db_path=db_path or tmp_path / 'state.db', read_only=read_only)
            self.closed = False
            opens['sessiondb'].append(self)

        def close(self):
            self.closed = True
            super().close()

    def connect(path, *a, **kw):
        opens['connect'] += 1
        return real_connect(path, *a, **kw)

    monkeypatch.setattr(hermes_state, 'SessionDB', Spy)
    monkeypatch.setattr(core, 'ro_connect', connect)
    return opens


# (sid, tid): proofs a pass asks, with repeats, as pending_closeouts does across rows.
PROOFS = [('root', 't_acked'), ('root', 't_other'), ('plain', 't_acked'), ('root', 't_acked'),
          ('plain', 't_other'), ('root', 't_third')]


def _prove():
    return [plugin.session_proof(sid, tid, {'completed_at': 100}) for sid, tid in PROOFS]


def test_a_pass_proves_the_same_with_one_open_of_each(world):
    alone = _prove()
    assert alone == [True, False, False, True, False, False]
    assert world['connect'] == len(PROOFS) and len(world['sessiondb']) == len(PROOFS)  # one of each per proof
    world['connect'], world['sessiondb'][:] = 0, []
    with plugin._proof_pass():
        in_pass = _prove()
        with plugin._proof_pass():  # nested: the outer pass's reads
            assert plugin.session_proof('root', 't_acked', {'completed_at': 100})
    assert in_pass == alone
    assert world['connect'] == 1
    assert [(d.read_only, d.closed) for d in world['sessiondb']] == [(True, True)]
    assert plugin._pass_local.res is None


def test_the_waiting_source_runs_its_pass_inside_one_proof_pass(world, tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    seen = []

    def pending(ledger, read, proof=None):
        seen.append(plugin._pass_local.res is not None)
        return {sid: [tid] for sid, tid in PROOFS if proof(sid, tid, {'completed_at': 100})}

    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(core, 'pending_closeouts', pending)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    monkeypatch.delitem(plugin._state, 'waiting_cache', raising=False)
    assert plugin.waiting_source() == {'root': ['t_acked']}
    assert seen == [True] and world['connect'] == 1 and len(world['sessiondb']) == 1
    assert world['sessiondb'][0].closed and plugin._pass_local.res is None


def test_remote_cards_take_the_origin_rows_from_cores_cached_projection(tmp_path, monkeypatch):
    import hermes_constants
    (tmp_path / 'kanban').mkdir()
    (tmp_path / 'kanban' / 'origin-ledger.db').write_bytes(b'')
    monkeypatch.setattr(hermes_constants, 'get_hermes_home', lambda: tmp_path)
    released = {'key': 'gw/p/b/t_released', 'via': 'origin', 'unfinished': False}
    open_row = {'key': 'gw/p/b/t_open', 'via': 'origin', 'unfinished': True}
    legacy = {'key': 'gw/b/t_legacy', 'via': 'shepherd'}
    ksa = types.SimpleNamespace(
        remote_links_and_origin_rows=lambda: ([legacy, open_row], [released, open_row]),
        _remote_links=lambda: pytest.fail('cards come with the rows'))
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', ksa)
    import hermes_cli
    monkeypatch.setattr(hermes_cli, 'kanban_session_activity', ksa, raising=False)
    import hermes_cli.kanban_origin_ledger as ledger
    monkeypatch.setattr(ledger, 'project', lambda *a, **k: pytest.fail('no second ledger projection'))
    assert plugin.remote_cards() == [legacy, released, open_row]

"""Cross-pass transcript proofs (IDLE-READS.md "Transcript proofs"): the sidebar's waiting source, the pre-LLM hook
and the waker each proved every finished card from scratch, re-scanning the chat's transcript since the card
finished on every pass (every ~4.5 s for the sidebar, every LLM call for the hook). An answered-notice index is now
kept per (state.db, lineage) and reused while that lineage's write-side transcript key (active row count, highest
id, id and timestamp sums, the sessions' trigger-bumped ``transcript_revision``) is unchanged. Churn elsewhere in
state.db (other chats, session bookkeeping) does not re-scan; any change to this lineage's rows does."""
import sqlite3
import time

import pytest
from test_closeout import plugin, core

hermes_state = pytest.importorskip('hermes_state')

NOTICE = '✔ [default] @default Kanban {} done — local'


@pytest.fixture
def world(tmp_path, monkeypatch):
    """state.db (real SessionDB schema: transcript_revision + its triggers) with a compressed chat root -> tip whose
    tip answered t_acked's notice, an unrelated busy chat, and a counter of transcript scans."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    path = tmp_path / 'state.db'
    db = hermes_state.SessionDB(db_path=path)
    db.create_session('root', source='cli')
    db.end_session('root', 'compression')
    db.create_session('tip', source='cli', parent_session_id='root')
    db.create_session('plain', source='cli')
    db.create_session('busy', source='cli')
    db.close()
    with sqlite3.connect(path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {'messages_transcript_revision_update', 'messages_display_revision_update'} <= names
        _say(conn, 'tip', 'user', NOTICE.format('t_acked'), 101)
        _say(conn, 'tip', 'assistant', 'Result checked.', 102, 'stop')
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [path])
    scans = []
    real = core.AckIndex._extend

    def counted(self, since, pool):
        scans.append((tuple(self.ids), since))
        return real(self, since, pool)

    monkeypatch.setattr(core.AckIndex, '_extend', counted)
    getattr(plugin, '_ack_memo_reset', lambda: None)()
    yield {'path': path, 'scans': scans}
    getattr(plugin, '_ack_memo_reset', lambda: None)()


def _say(conn, sid, role, text, at, finish=None):
    return conn.execute('INSERT INTO messages (session_id, role, content, timestamp, finish_reason, active) '
                        'VALUES (?,?,?,?,?,1)', (sid, role, text, at, finish)).lastrowid


PROOFS = [('root', 't_acked'), ('root', 't_other'), ('plain', 't_acked'), ('root', 't_acked'), ('plain', 't_other')]


def _pass():
    with plugin._proof_pass():
        return [plugin.session_proof(sid, tid, {'completed_at': 100}) for sid, tid in PROOFS]


def _churn(path):
    """Heartbeat-like churn: another chat talks, session bookkeeping is rewritten. None of it is this lineage's."""
    with sqlite3.connect(path) as conn:
        _say(conn, 'busy', 'user', 'hello', time.time())
        _say(conn, 'busy', 'assistant', 'hi', time.time(), 'stop')
        conn.execute("UPDATE sessions SET title = ? WHERE id = 'busy'", (str(time.time()),))
        conn.execute("UPDATE sessions SET message_count = message_count + 1 WHERE id = 'tip'")


def _uncached():
    """What a cold pass proves now (no cross-pass memo)."""
    getattr(plugin, '_ack_memo_reset', lambda: None)()
    return _pass()


def test_unchanged_lineage_is_not_rescanned_across_passes(world):
    first = _pass()
    assert first == [True, False, False, True, False]
    scanned = len(world['scans'])
    assert scanned == 2  # one scan per lineage (root+tip, plain)
    for _ in range(3):
        _churn(world['path'])
        assert _pass() == first
    assert len(world['scans']) == scanned  # heartbeat churn only: nothing re-read


def test_the_pre_llm_hook_proves_inside_one_pass_and_shares_the_memo(world, tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    card = {'id': 't_acked', 'status': 'done', 'completed_at': 100.0}
    other = {**card, 'id': 't_other'}
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_upgraded', lambda *a: None)
    monkeypatch.setattr(plugin, '_eligible', lambda *a: True)
    monkeypatch.setattr(plugin, 'read_cards', lambda ids, *a, **k: core.Cards(
        {k: v for k, v in {'t_acked': card, 't_other': other}.items() if k in ids}))
    connects = []
    real_connect = core.ro_connect
    monkeypatch.setattr(core, 'ro_connect', lambda p, *a, **k: connects.append(p) or real_connect(p, *a, **k))
    _pass()  # the waiting source proved both lineages
    before = len(world['scans'])
    connects.clear()
    for _ in range(3):
        led.write('root', batch=['t_other', 't_acked'], open=[], covered=[], closing=None)
        plugin.pre_llm_call(session_id='root', platform='cli')
        row = led.get('root')
        assert 't_acked' not in row['batch'] and 't_other' in row['batch']  # proven card left, unproven stays
    assert len(world['scans']) == before  # the hook reused the waiting pass's index
    assert len(connects) == 3  # one state.db connection per hook call, not one per proof


def test_a_new_answer_in_the_lineage_is_proven_on_the_next_pass(world):
    assert _pass()[1] is False
    with sqlite3.connect(world['path']) as conn:
        _say(conn, 'tip', 'user', NOTICE.format('t_other'), 103)
        _say(conn, 'tip', 'assistant', 'Other checked.', 104, 'stop')
    assert _pass()[1] is True
    assert _pass() == _uncached()


@pytest.mark.parametrize('rewrite', [
    "UPDATE messages SET content = 'Not a notice' WHERE session_id = 'tip' AND role = 'user'",
    "UPDATE messages SET display_kind = 'commentary' WHERE session_id = 'tip' AND role = 'assistant'",
    "UPDATE messages SET active = 0 WHERE session_id = 'tip' AND role = 'assistant'",
    "UPDATE messages SET timestamp = 99 WHERE session_id = 'tip' AND role = 'user'",
    "UPDATE messages SET tool_calls = '[{\"id\": \"x\"}]' WHERE session_id = 'tip' AND role = 'assistant'",
    "DELETE FROM messages WHERE session_id = 'tip' AND role = 'assistant'",
])
def test_any_rewrite_of_the_lineage_rows_is_reread(world, rewrite):
    assert _pass()[0] is True
    with sqlite3.connect(world['path']) as conn:
        conn.execute(rewrite)
    assert _pass()[0] is False  # the answered notice no longer proves: never a stale green
    assert _pass() == _uncached()


def test_deactivate_one_row_and_restore_another_is_reread(world):
    """Same active count and highest id, other rows: the id sum moves."""
    with sqlite3.connect(world['path']) as conn:
        hidden = _say(conn, 'tip', 'user', NOTICE.format('t_other'), 103)
        conn.execute('UPDATE messages SET active = 0 WHERE id = ?', (hidden,))
        last = _say(conn, 'tip', 'assistant', 'Filler.', 105, 'stop')
    assert _pass()[1] is False
    scanned = len(world['scans'])
    with sqlite3.connect(world['path']) as conn:
        conn.execute('UPDATE messages SET active = 1 WHERE id = ?', (hidden,))
        conn.execute("UPDATE messages SET active = 0 WHERE session_id = 'tip' AND id NOT IN (?, ?) "
                     "AND role = 'user'", (hidden, last))
        count, top = conn.execute("SELECT COUNT(*), MAX(id) FROM messages WHERE session_id = 'tip' "
                                  "AND active = 1").fetchone()
    assert (count, top) == (3, last)  # neither the count nor the highest id moved
    assert _pass()[1] is True and len(world['scans']) > scanned


def test_no_transcript_revision_means_no_reuse(world, monkeypatch):
    with sqlite3.connect(world['path']) as conn:
        conn.execute('DROP TRIGGER messages_display_revision_update')
    _pass()
    scanned = len(world['scans'])
    _pass()
    assert len(world['scans']) == 2 * scanned  # unkeyable: scanned every pass, as before


def test_replaced_state_db_is_reread(world, tmp_path):
    _pass()
    scanned = len(world['scans'])
    copy = tmp_path / 'copy.db'
    with sqlite3.connect(world['path']) as src, sqlite3.connect(copy) as dst:
        src.backup(dst)
    for suffix in ('-wal', '-shm'):
        (tmp_path / ('state.db' + suffix)).unlink(missing_ok=True)
    copy.replace(world['path'])  # same content, new file
    _pass()
    assert len(world['scans']) == 2 * scanned


def test_a_lower_since_extends_the_shared_index(world):
    _pass()
    scanned = len(world['scans'])
    with plugin._proof_pass():
        assert plugin.session_proof('root', 't_acked', {'completed_at': 50})
    assert world['scans'][scanned:] == [(('root', 'tip'), 50.0)]
    with plugin._proof_pass():
        assert plugin.session_proof('root', 't_acked', {'completed_at': 60})
    assert len(world['scans']) == scanned + 1


def test_memo_is_bounded(world, monkeypatch):
    monkeypatch.setattr(plugin, '_ACK_MEMO_CAP', 2)
    for sid in ('root', 'plain', 'busy', 'tip'):
        with plugin._proof_pass():
            plugin.session_proof(sid, 't_acked', {'completed_at': 100})
    assert len(plugin._ack_memo) == 2


def test_threads_share_one_index_consistently(world):
    """The sidebar, a hook and the waker prove from different threads: the shared index answers each the same."""
    import threading
    _pass()
    out, errors = [], []

    def worker(since):
        try:
            for _ in range(20):
                with plugin._proof_pass():
                    out.append((plugin.session_proof('root', 't_acked', {'completed_at': since}),
                                plugin.session_proof('root', 't_other', {'completed_at': since})))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in (100, 60, 30, 10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and out == [(True, False)] * 80


@pytest.mark.parametrize('at_call', [1, 2])
def test_a_write_between_key_and_rows_is_never_labelled_with_the_older_key(world, monkeypatch, at_call):
    """A reply lands after a key read (1: the pass's validation read; 2: inside the index's read snapshot); then
    /undo deactivates that exchange, which restores every count and sum of the older key (``active`` is in no
    trigger). The index must not be reused as if it had been read under that older key (review finding: ABA)."""
    real = core.transcript_key
    state = {'calls': 0, 'ids': []}

    def key_then_write(conn, ids):
        k = real(conn, ids)
        if 'tip' in ids:
            state['calls'] += 1
            if state['calls'] == at_call:
                with sqlite3.connect(world['path']) as w:
                    state['ids'] = [_say(w, 'tip', 'user', NOTICE.format('t_other'), 103),
                                    _say(w, 'tip', 'assistant', 'Other checked.', 104, 'stop')]
        return k

    monkeypatch.setattr(core, 'transcript_key', key_then_write)
    first = _pass()
    monkeypatch.setattr(core, 'transcript_key', real)
    # 1: the index read after the reply (it is seen); 2: the index's snapshot predates it (seen next pass)
    assert first[1] is (at_call == 1)
    with sqlite3.connect(world['path']) as w:
        w.execute(f"UPDATE messages SET active = 0 WHERE id IN ({state['ids'][0]}, {state['ids'][1]})")
    assert _pass() == _uncached() == [True, False, False, True, False]


def test_a_trigger_naming_columns_only_outside_its_of_list_does_not_vouch(world):
    with sqlite3.connect(world['path']) as conn:
        conn.executescript("""
            DROP TRIGGER messages_display_revision_update;
            CREATE TRIGGER messages_display_revision_update AFTER UPDATE OF display_metadata ON messages
            WHEN new.display_kind IS NOT old.display_kind  -- display_kind
            BEGIN UPDATE sessions SET transcript_revision = transcript_revision + 1 WHERE id = new.session_id; END;
        """)
        assert core.transcript_key(conn, ['tip']) is None


def test_a_lineage_absent_from_a_state_db_is_not_kept(world):
    with plugin._proof_pass():
        plugin.session_proof('nobody', 't_acked', {'completed_at': 100})
    assert not any(k[1] == ('nobody',) for k in plugin._ack_memo)

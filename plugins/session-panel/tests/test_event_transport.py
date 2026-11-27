"""The bounded event tail crosses SQLite/Python once, retaining canonical events."""
import sqlite3

import pytest

pytest.importorskip('hermes_cli.kanban_db')
pytest.importorskip('hermes_state_page_pack')
from test_core_readers import api  # noqa: F401 -- same real API fixture


@pytest.fixture
def events():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,payload,created_at,run_id,uid)')
    c.executemany('INSERT INTO task_events VALUES(?,?,?,?,?,?,?)', [
        (i, 't_card', 'heartbeat' if i % 7 == 0 else 'commented', '{"n":%d}' % i, i // 3, None, 'event-%d' % i)
        for i in range(100)])
    c.execute("INSERT INTO task_events VALUES(200,'t_other','commented','{}',900,NULL,NULL)")
    try:
        yield c
    finally:
        c.close()


def legacy(api, c, tid='t_card'):
    from hermes_cli.kanban_db import Event
    rows = c.execute("SELECT * FROM task_events WHERE task_id=? AND kind!='heartbeat' "
                     'ORDER BY created_at DESC,id DESC LIMIT ?', (tid, api.EVENTS_KEPT)).fetchall()
    return [Event.from_row(r) for r in reversed(rows)]


def test_event_tail_one_handoff(api, events):
    expected = legacy(api, events)
    handed = []
    def record(c, row):
        handed.append(1)
        return sqlite3.Row(c, row)
    events.row_factory = record
    assert api._newest_events(events, 't_card') == expected
    assert len(expected) == api.EVENTS_KEPT
    assert len(handed) == 1, 'bounded event rows must arrive in one transport cell'


def test_aggregate_overflow_retains_individually_readable_events(api, events):
    events.execute('UPDATE task_events SET payload=?', ('{"body":"' + 'a' * 700 + '"}',))
    expected = legacy(api, events)
    events.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4096)
    assert api._newest_events(events, 't_card') == expected


@pytest.mark.parametrize('count', [0, 1, 39, 40, 41, 95])
def test_exact_cap_order_and_filter(api, events, count):
    events.execute('DELETE FROM task_events WHERE id>=?', (count,))
    assert api._newest_events(events, 't_card') == legacy(api, events)
    assert api._newest_events(events, 't_missing') == []


@pytest.mark.parametrize('payload', [None, '', '{}', '[]', '7', 'bad json', '{"x":"雪\\\\n\\\\u0000"}',
                                     b'{"binary":true}', b'\xff', 9223372036854775807, 1.0000000000000002])
def test_event_value_conversion_parity(api, events, payload):
    events.execute('UPDATE task_events SET payload=?,kind=?,created_at=?,run_id=?,uid=?',
                   (payload, b'commented', 1234.125, '5', 'unique'))
    assert api._newest_events(events, 't_card') == legacy(api, events)


def test_missing_optional_schema_columns(api, events):
    events.execute('ALTER TABLE task_events DROP COLUMN run_id')
    events.execute('ALTER TABLE task_events DROP COLUMN uid')
    assert api._newest_events(events, 't_card') == legacy(api, events)


def test_old_core_without_helper(api, events, monkeypatch):
    import sys
    expected = legacy(api, events)
    monkeypatch.setitem(sys.modules, 'hermes_state_page_pack', None)
    assert api._newest_events(events, 't_card') == expected


@pytest.mark.parametrize('error', [RuntimeError('helper failed'), sqlite3.DataError('not overflow'),
                                  sqlite3.OperationalError('reader failed')])
def test_helper_failure_is_not_rescued(api, events, monkeypatch, error):
    import hermes_state_page_pack as packing
    def fail(*args):
        raise error
    monkeypatch.setattr(packing, 'read_page', fail)
    with pytest.raises(type(error), match=str(error)):
        api._newest_events(events, 't_card')


@pytest.mark.parametrize('lossy', [False, True])
def test_invalid_text_keeps_existing_failure_or_conversion(api, events, lossy):
    from hermes_cli.kanban_db import _lossy_text
    if lossy:
        events.text_factory = _lossy_text
    events.execute("UPDATE task_events SET payload=CAST(X'FF' AS TEXT)")
    if lossy:
        assert api._newest_events(events, 't_card') == legacy(api, events)
    else:
        with pytest.raises(sqlite3.OperationalError):
            legacy(api, events)
        with pytest.raises(sqlite3.OperationalError):
            api._newest_events(events, 't_card')

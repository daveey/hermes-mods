"""Read-only receipt API and remote binding identity controls."""
from types import SimpleNamespace
import pytest
from test_remote_card_courier import remote_row
from test_kanban_mirror import fixture
from test_withdrawal import ROOT, OWNER, load_module


def test_remote_projection_id_excludes_display_title(tmp_path):
    mirror, _, _, hr = fixture(tmp_path)
    first = next(mirror.remote._snapshot(OWNER, remote_row(hr)))
    second = next(mirror.remote._snapshot(OWNER, dict(remote_row(hr), title='New display')))
    assert mirror._request(OWNER, 'other', first)['requestId'] == mirror._request(OWNER, 'other', second)['requestId']
    third = next(mirror.remote._snapshot(OWNER, dict(remote_row(hr), card_answer_generation='b' * 64)))
    assert mirror._request(OWNER, 'other', first)['requestId'] != mirror._request(OWNER, 'other', third)['requestId']


@pytest.mark.parametrize('status,data,expected', [(404, {}, None), (200, {'request': {'state': 'done'}}, {'state': 'done'}),
    (503, {}, 'error'), (200, {}, 'error'), (200, {'request': []}, 'error')])
def test_courier_retirement_read_is_read_only(tmp_path, status, data, expected):
    module = load_module('receipt_reader', ROOT / 'remote_cards.py')
    calls = []
    client = SimpleNamespace(load_config=lambda: {'hub_url': 'https://example.test'}, read_key=lambda: 'fixture',
        Api=lambda *args: SimpleNamespace(call=lambda method, path: calls.append((method, path)) or (status, data)))
    reader = module.Courier.__new__(module.Courier)
    reader.view = SimpleNamespace(load_client=lambda: client)  # no open_db, queue_request, send_now allowed
    if expected == 'error':
        with pytest.raises(OSError):
            reader.read({}, 'operation-receipt')
    else:
        assert reader.read({}, 'operation-receipt') == expected
    assert calls == [('GET', '/v2/sync/requests/operation-receipt')]


@pytest.mark.parametrize('field,value', [('title', 42), ('title', {}), ('title', []),
    ('action', 42), ('action', {}), ('action', []), ('choices', [{'label': 42, 'value': 'x'}]),
    ('choices', [{'label': 'X', 'value': {}}])])
def test_invalid_display_fields_preserve_frozen_tile(tmp_path, field, value):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    binding = mirror.binding(OWNER, old['requestId'])
    if field == 'title':
        row['title'] = value
    else:
        row['human_request'] = dict(hr, **{field: value})
    with pytest.raises(OSError):
        list(mirror.remote._snapshot(OWNER, dict(row, card_answer_generation='b' * 64)))
    mirror.remote.cards = lambda owner: [dict(row, card_answer_generation='b' * 64), dict(remote_row(hr), id='t_good')]
    mirror.remote.reconcile(OWNER)
    assert mirror.binding(OWNER, old['requestId']) == binding
    assert store.get(OWNER, old['requestId'])['state'] == 'open'
    assert len([r for r in store.requests(OWNER) if r['state'] == 'open']) == 2

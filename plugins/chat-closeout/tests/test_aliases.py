"""Legacy identities are removable only with a found sibling in this batch."""
import pytest
from test_closeout import core, plugin

CANON = 'hub/default/default/t_one'


@pytest.mark.parametrize('alias', ['t_one', 'hub/default/t_one'])
def test_alias_only_obstacle_closes_out(tmp_path, monkeypatch, alias):
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('chat', batch=[alias, CANON], covered=[alias])
    cards = {CANON: {'status': 'done'}}
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_session_source', lambda sid: 'desktop')
    calls = []
    assert plugin.wake_tick(now=1000, wake=lambda *a, **k: calls.append(k) or 'started',
                            unseen={}, last_user=lambda _: 0, read=lambda _: cards,
                            proof=lambda *a: False)
    assert calls[0]['display_metadata']['task_ids'] == [CANON]
    assert alias not in led.get('chat')['covered']
    core.record_turn_end(led, 'chat', [], lambda _: cards, now=1001)
    assert led.get('chat')['batch'] == []


@pytest.mark.parametrize('alias', ['t_one', 'hub/default/t_one'])
def test_open_canonical_stays_pending(alias):
    row = {'batch': [alias, CANON], 'covered': [alias]}
    cards = {CANON: {'status': 'running'}}
    row.update(core.reconcile(row, cards))
    assert set(row['batch']) == {CANON}
    assert not row['covered']
    assert core.pending_key(row, cards) is None


@pytest.mark.parametrize('alias,sibling,cards', [
    ('t_one', None, {}),
    ('t_one', CANON, {}),
    ('hub/default/t_one', CANON, {}),
    ('hub/default/t_one', 'elsewhere/default/default/t_one', {'elsewhere/default/default/t_one': {'status': 'done'}}),
    ('hub/default/t_one', 'hub/other/default/t_one', {'hub/other/default/t_one': {'status': 'done'}}),
    ('default/t_one', CANON, {CANON: {'status': 'done'}}),
    ('hub/default/other/t_one', CANON, {CANON: {'status': 'done'}}),
    ('t_one', CANON, {CANON: {'status': 'done'}, 't_one': {'status': 'running'}}),
    ('hub/default/t_one', CANON, {CANON: {'status': 'done'}, 'hub/default/t_one': {'status': 'running'}}),
    ('t_one', None, {CANON: {'status': 'done'}}),
])
def test_not_proven_alias_is_retained(alias, sibling, cards):
    row = {'batch': [alias] + ([sibling] if sibling else []), 'covered': []}
    row.update(core.reconcile(row, cards))
    assert alias in row['batch']
    assert core.pending_key(row, cards) is None


def test_covered_canonical_and_alias_retire_together():
    row = {'batch': ['t_one', CANON], 'covered': ['t_one', CANON]}
    row.update(core.reconcile(row, {CANON: {'status': 'done'}}))
    assert not row['batch'] and not row['covered']


def test_bare_alias_of_found_board_key():
    row = {'batch': ['t_one', 'board/t_one'], 'covered': ['t_one']}
    row.update(core.reconcile(row, {'board/t_one': {'status': 'done'}}))
    assert row['batch'] == {'board/t_one'}
    assert not row['covered']

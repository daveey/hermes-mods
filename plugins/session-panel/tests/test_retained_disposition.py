"""Settled retained disposition through the real board adapter, not event-tail guesses."""
import json
import pytest
from test_core_readers import api, board, readers_for, kanban_db
from test_model import model, task, bundle, NOW, human

TEXT = 'stopped · incomplete · evidence retained · no action needed'


from retained_fixture import retained_bundle


def one(b):
    return next(r for rows in model.reduce_panel(b, now=NOW)['sections'].values() for r in rows)


@pytest.mark.parametrize('replica', [False, True])
@pytest.mark.parametrize('backfill', [False, True])
def test_retained_stop_outranks_later_spend_review(api, board, monkeypatch, replica, backfill):
    path, conn = board
    b = retained_bundle(api, conn, readers_for(api, path, monkeypatch), replica=replica, backfill=backfill)
    row = one(b)
    assert row['detail'].startswith(TEXT)
    assert 'owner has not decided' not in row['detail']
    assert 'waiting on' not in row['detail']
    assert row['retained_disposition']['text'] == TEXT
    assert not model.reduce_panel(b, now=NOW)['sections']['done']


@pytest.mark.parametrize('case', ['pending', 'human', 'newer', 'missing', 'foreign', 'running'])
def test_retained_stop_does_not_mask_live_or_unknown_evidence(api, board, monkeypatch, case):
    path, conn = board
    b = retained_bundle(api, conn, readers_for(api, path, monkeypatch), pending=case == 'pending', newer=case == 'newer')
    raw = b['tasks'][0]; detail = b['cards'][raw['id']]['detail']
    if case == 'human':
        raw.update(human_request=human(), block_kind='needs_input', wait={'wait_state': 'human'})
    if case == 'missing':
        detail['task'].pop('retained_stop', None)
    if case == 'foreign':
        raw['gateway'] = 'another-owner'
    if case == 'running':
        raw['status'] = 'running'
    row = one(b)
    assert 'retained_disposition' not in row
    assert not row['detail'].startswith(TEXT)
    if case == 'human':
        assert model.reduce_panel(b, now=NOW)['sections']['needs_you']
    if case == 'pending':
        assert 'owner has not decided' in row['detail']


def test_stale_retained_stop_keeps_disposition_separate_from_current_trust(api, board, monkeypatch):
    path, conn = board
    b = retained_bundle(api, conn, readers_for(api, path, monkeypatch), replica=True)
    b['tasks'][0].update(stale=True, synced_at=NOW - 400)
    row = one(b)
    assert row['detail'].startswith(TEXT)
    assert row['evidence_state'] == 'unknown'
    assert 'last recorded disposition' in row['detail']
    assert any('freshness' in text.lower() for text in row['details'])
    assert not row.get('wait_kind')

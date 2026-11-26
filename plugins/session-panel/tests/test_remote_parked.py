"""Stale worker-homed cards retain historical state, not current wait authority."""
from test_model import NOW, bundle, model, task


def remote(status='scheduled', **kw):
    return task(status=status, remote=True, source='replica', gateway='worker-a3', **kw)


def rows(m):
    return m['sections']['waiting'] + m['sections']['ready']


def test_remote_scheduled_fresh_sync_reads_parked_with_gateway_and_age():
    m = model.reduce_panel(bundle(remote(stale=True, synced_at=NOW - 20)), now=NOW)
    row = rows(m)[0]
    assert row['detail'].startswith('status unknown · last known:')
    assert row['evidence_state'] == 'unknown'
    assert not row.get('wait_kind') and not row.get('wait_eta')
    assert not m['sections']['needs_you'] and not m['sections']['stuck']
    assert 'scheduled' in row['detail']
    assert 'on worker-a3 (synced 20s ago)' in row['detail']
    assert row['parked'] is True


def test_remote_scheduled_stale_sync_reads_aged_board_state():
    m = model.reduce_panel(bundle(remote(stale=True, synced_at=NOW - 1200)), now=NOW)
    row = rows(m)[0]
    assert row['detail'].startswith('status unknown · last known:')
    assert row['evidence_state'] == 'unknown'
    assert not row.get('wait_kind') and not row.get('wait_eta')
    assert not m['sections']['needs_you'] and not m['sections']['stuck']
    assert 'scheduled' in row['detail']
    assert 'as of ' in row['detail'] and 'worker-a3 not synced for 20 min' in row['detail']


def test_remote_running_with_fresh_observation_is_unchanged():
    t = task(status='running', remote=True, source='replica', gateway='worker-a3', synced_at=NOW - 20,
             execution_observations=[])
    m = model.reduce_panel(bundle(t), now=NOW)
    row = (m['sections']['running'] + m['sections']['waiting'])[0]
    assert 'synced' not in row['detail']  # the parked wording never applies to running cards


def test_real_contradictions_still_read_unknown():
    for extra in ({'synced_at': NOW + 3600},                              # future sync clock
                  {},                                                     # no sync clock at all
                  {'synced_at': NOW - 20, 'partial_source_unknown': True}):  # source could not be read
        m = model.reduce_panel(bundle(remote(stale=True, **extra)), now=NOW)
        assert 'status unknown' in rows(m)[0]['detail'], extra

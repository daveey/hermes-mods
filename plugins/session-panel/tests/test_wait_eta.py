"""ETA values are producer-owned; the reducer only carries them to the wire."""
import pytest
from test_model import NOW, bundle, model, task


@pytest.mark.parametrize('kind,on', [
    ('clock', 'the clock until 2026-11-24 14:30 UTC'),
    ('ci', 'ci example/repo'), ('deploy', 'deploy example-service')])
@pytest.mark.parametrize('expected', [None, 1500])
@pytest.mark.parametrize('wire', [False, True])
def test_wait_eta_pass_through(kind, on, expected, wire):
    eta = dict(waited_since=NOW - 720, expected_s=expected, estimate_source='fixture history')
    row = task(status='scheduled', wait=dict(wait_state='external_running', waiting_on=on,
               waiting_since=NOW - 720, **eta))
    result = model.reduce_panel(bundle(row), now=NOW, wire=wire)
    painted = result['sections']['waiting'][0]
    assert painted['wait_kind'] == kind
    assert painted['wait_eta'] == eta
    assert result['header']['wait_kind'] == kind
    assert result['header']['pips'][0]['wait_kind'] == kind


def test_real_core_clock_eta_reaches_model():
    waits = pytest.importorskip('hermes_cli.kanban_wait_info')
    park = {'awaiting': {'kind': 'clock', 'why': 'soak'}, 'wake_at': NOW + 600}
    wait = waits.describe('scheduled', park=park, since=NOW - 300, now=NOW)
    row = task(status='scheduled', wait=wait)
    painted = model.reduce_panel(bundle(row), now=NOW, wire=True)['sections']['waiting'][0]
    assert painted['wait_kind'] == 'clock'
    assert painted['wait_eta'] == {k: wait[k] for k in ('waited_since', 'expected_s', 'estimate_source')}
    assert painted['wait_eta']['expected_s'] == 900


def test_non_delivery_omits_eta():
    row = task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': 'external vendor',
               'waited_since': NOW - 60, 'expected_s': 120})
    painted = model.reduce_panel(bundle(row), now=NOW)['sections']['waiting'][0]
    assert 'wait_eta' not in painted
    assert 'wait_kind' not in painted

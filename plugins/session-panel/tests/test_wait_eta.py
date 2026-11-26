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


WHY = 'Event watch on hub-hosted original owners; backstop at nightly mini rollout 10:30Z'


def _clock_row(on, **kw):
    return task(status='scheduled', wait=dict(wait_state='waiting', waiting_on=on, waiting_since=NOW - 300, **kw))


def _text(detail):
    return detail if isinstance(detail, str) else ''.join(p if isinstance(p, str) else str(p.get('text', '')) for p in detail)


def test_clock_wait_names_prerequisite_with_timer_secondary():
    row = _clock_row(f'the clock until 2026-11-24 14:30 UTC ({WHY})')
    painted = model.reduce_panel(bundle(row), now=NOW)['sections']['waiting'][0]
    assert painted['detail'].startswith('waiting on Event watch on hub-hosted')
    assert 'left (until' in painted['detail']
    assert painted['wait_kind'] == 'clock'
    assert any(WHY in d for d in painted['details'])
    assert any(d.startswith('Next actor: the clock') for d in painted['details'])


def test_clock_wait_without_reason_says_unavailable_not_contention():
    painted = model.reduce_panel(bundle(_clock_row('the clock until 2026-11-24 14:30 UTC')), now=NOW)['sections']['waiting'][0]
    assert painted['detail'].startswith('reason unavailable')
    assert 'worker' not in painted['detail'] and 'capacity' not in painted['detail']
    assert any('unavailable (the park recorded none)' in d for d in painted['details'])


def test_clock_reason_does_not_churn_the_wire_revision():
    row = _clock_row(f'the clock until 2026-11-24 14:30 UTC ({WHY})')
    a = model.reduce_panel(bundle(row), now=NOW, wire=True)
    b = model.reduce_panel(bundle(row), now=NOW + 7, wire=True)
    assert a['revision'] == b['revision']
    assert 'Event watch on hub-hosted' in _text(a['sections']['waiting'][0]['detail'])


def test_real_core_clock_why_reaches_the_row():
    waits = pytest.importorskip('hermes_cli.kanban_wait_info')
    park = {'awaiting': {'kind': 'clock', 'why': WHY}, 'wake_at': NOW + 600}
    wait = waits.describe('scheduled', park=park, since=NOW - 300, now=NOW)
    painted = model.reduce_panel(bundle(task(status='scheduled', wait=wait)), now=NOW)['sections']['waiting'][0]
    assert 'waiting on Event watch on hub-hosted' in painted['detail']
    assert 'left (until' in painted['detail']
    assert any(WHY[:70] in d for d in painted["details"])  # core before the 200-char label keeps 80


def test_ci_and_deploy_waits_keep_precedence():
    for on, phrase in (('ci example/repo', 'waiting on CI'), ('deploy hub service', 'waiting on hub deploy')):
        painted = model.reduce_panel(bundle(_clock_row(on)), now=NOW)['sections']['waiting'][0]
        assert painted['detail'].startswith(phrase)

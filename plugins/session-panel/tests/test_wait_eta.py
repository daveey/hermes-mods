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


WHY = 'Event watch on service owners; backstop at nightly rollout 10:30Z'
WATCHED = ['t_0000000000000001', 't_0000000000000002', 't_0000000000000003', 't_0000000000000004']
CLOCK = 'the clock until 2026-11-24 14:30 UTC'  # NOW + 2d 21h
TIMER = '2d 21h left (until 2026-11-24 06:30 PST)'
HOST = 'long-worker-hostname.internal.example.com'


def _clock_row(on, **kw):
    extra = {k: kw.pop(k) for k in ('gateway', 'hostname', 'stale') if k in kw}
    return task(status='scheduled', wait=dict(wait_state='waiting', waiting_on=on, waiting_since=NOW - 300, **kw), **extra)


def _text(detail):
    return detail if isinstance(detail, str) else ''.join(p if isinstance(p, str) else str(p.get('text', '')) for p in detail)


def _painted(row, **kw):
    return model.reduce_panel(bundle(row), now=NOW, **kw)['sections']['waiting'][0]


def test_clock_wait_names_prerequisite_and_keeps_the_whole_timer():
    painted = _painted(_clock_row(f'{CLOCK} ({WHY[:80]})', prerequisite=WHY, next_actor='the clock (timed check)'))
    assert painted['detail'].startswith('waiting on Event watch on service owners')
    assert painted['detail'].endswith(f' · 5m · {TIMER} on remote')
    assert len(painted['detail']) <= model.WAIT_MAX + 1
    assert painted['wait_kind'] == 'clock' and painted['wait_reason'] == WHY
    assert painted['wait_next_actor'] == 'the clock (timed check)'
    assert f'Waiting reason: {WHY}' in painted['details']  # the full wording, not the clipped line
    assert 'Next actor: the clock (timed check)' in painted['details']
    assert any(d.startswith('Next check: the clock at 2026-11-24 06:30 PST') for d in painted['details'])


@pytest.mark.parametrize('reason', [WHY, WHY * 5, 'soak'])
@pytest.mark.parametrize('hostname', ['', HOST])
def test_timer_and_location_are_never_clipped_by_the_reason(reason, hostname):
    row = _clock_row(CLOCK, prerequisite=reason, gateway='hermes-hub', hostname=hostname)
    location = ' on hermes-hub' + (f' ({hostname})' if hostname else '')
    plain = _painted(row)['detail']
    assert plain.endswith(f' · 5m · {TIMER}{location}')
    assert plain.startswith('waiting on ' + reason[:20])
    wire = _text(_painted(row, wire=True)['detail'])
    assert wire.endswith(f' left (until 2026-11-24 06:30 PST){location}')
    assert wire.startswith('waiting on ' + reason[:20])
    overdue = model.reduce_panel(bundle(row, computed_at=NOW + 4 * 86400), now=NOW + 4 * 86400)['sections']['waiting'][0]['detail']
    assert f'(was due 2026-11-24 06:30 PST){location}' in overdue and overdue.endswith(location)
    assert 'overdue by' in overdue and overdue.startswith('waiting on ' + reason[:20])


def test_event_watch_with_backstop_names_the_watched_cards_not_the_clock():
    actor = 'cards ' + ', '.join(WATCHED) + ' (event watch)'
    painted = _painted(_clock_row(f'{CLOCK} ({WHY[:80]})', prerequisite=WHY, watching=WATCHED, next_actor=actor,
                                  backstop_at=NOW + 600))
    assert ' · watching 4 cards · 5m · 2d 21h left (backstop 2026-11-24 06:30 PST) on remote' in painted['detail']
    assert '(until' not in painted['detail']
    assert f'Next actor: {actor}' in painted['details']
    assert any(d.startswith('Next check: clock backstop at 2026-11-24 06:30 PST') for d in painted['details'])
    assert not any('Next actor: the clock' in d for d in painted['details'])
    assert painted['wait_kind'] == 'clock'  # still a clock-coloured wait with its ETA


def test_clock_wait_without_reason_says_unavailable_not_contention():
    painted = _painted(_clock_row(CLOCK))
    assert painted['detail'] == f'waiting · reason unavailable · 5m · {TIMER} on remote'
    assert 'worker' not in painted['detail'] and 'capacity' not in painted['detail']
    assert 'Waiting reason: unavailable (the park recorded none)' in painted['details']
    assert 'wait_reason' not in painted


def test_legacy_label_only_reason_is_still_shown():
    """A core before `prerequisite` carries the why only inside the label, cut at 80 characters."""
    painted = _painted(_clock_row(f'{CLOCK} ({WHY[:80]})'))
    assert painted['detail'].startswith('waiting on Event watch on service owners')
    assert painted['detail'].endswith(f'{TIMER} on remote')
    assert painted['wait_reason'] == WHY[:80]
    assert 'Next actor: the clock (timed check)' in painted['details']


@pytest.mark.parametrize('bad', [7, True, [7], {'a': 1}, None])
def test_malformed_recorded_fields_are_ignored_not_rendered(bad):
    painted = _painted(_clock_row(CLOCK, prerequisite=bad, next_actor=bad, watching=bad, backstop_at='soon'))
    assert painted['detail'] == f'waiting · reason unavailable · 5m · {TIMER} on remote'
    assert 'Next actor: the clock (timed check)' in painted['details']


def test_stale_row_vouches_for_no_reason_or_actor():
    painted = _painted(_clock_row(CLOCK, prerequisite=WHY, next_actor='the clock (timed check)', stale=True))
    assert 'wait_reason' not in painted and 'wait_next_actor' not in painted
    assert painted['detail'].startswith('status unknown')
    assert not any(d.startswith(('Waiting reason:', 'Next actor:', 'Next check:')) for d in painted['details'])


def test_clock_reason_does_not_churn_the_wire_revision():
    row = _clock_row(CLOCK, prerequisite=WHY, watching=WATCHED, next_actor='cards (event watch)', backstop_at=NOW + 600)
    a = model.reduce_panel(bundle(row), now=NOW, wire=True)
    b = model.reduce_panel(bundle(row), now=NOW + 7, wire=True)
    assert a['revision'] == b['revision']
    assert 'Event watch on service owners' in _text(a['sections']['waiting'][0]['detail'])
    changed = _clock_row(CLOCK, prerequisite='Another reason', next_actor='the clock (timed check)')
    assert model.reduce_panel(bundle(changed), now=NOW, wire=True)['revision'] != a['revision']


def test_real_core_clock_why_reaches_the_row():
    waits = pytest.importorskip('hermes_cli.kanban_wait_info')
    park = {'awaiting': {'kind': 'clock', 'why': WHY}, 'wake_at': NOW + 600}
    wait = waits.describe('scheduled', park=park, since=NOW - 300, now=NOW)
    painted = _painted(task(status='scheduled', wait=wait))
    assert painted['detail'].startswith('waiting on Event watch on service owners')
    assert painted['detail'].endswith(' · 5m · 10m left (until 2026-11-21 09:20 PST) on remote')
    if 'prerequisite' in wait:  # a core with park_semantics publishes the whole reason
        assert f'Waiting reason: {WHY}' in painted['details']
    else:
        assert f'Waiting reason: {WHY[:80]}' in painted['details']


@pytest.mark.parametrize('on,phrase', [('ci example/repo', 'waiting on CI'), ('deploy hub service', 'waiting on hub deploy')])
def test_ci_and_deploy_waits_keep_precedence_and_gain_the_reason(on, phrase):
    plain = _painted(_clock_row(on))
    assert plain['detail'].startswith(phrase) and 'wait_reason' not in plain
    assert not any(d.startswith('Waiting reason:') for d in plain['details'])
    painted = _painted(_clock_row(on, prerequisite='exact-head checks', next_actor=on))
    assert painted['detail'] == plain['detail']  # the producer line is unchanged
    assert painted['wait_reason'] == 'exact-head checks' and painted['wait_next_actor'] == on
    assert 'Waiting reason: exact-head checks' in painted['details'] and f'Next actor: {on}' in painted['details']


def test_ordinary_timer_and_non_park_rows_are_untouched():
    for status, wait in (('todo', {}), ('ready', {}), ('running', {}),
                         ('scheduled', {'wait_state': 'waiting', 'waiting_on': 'external vendor'})):
        m = model.reduce_panel(bundle(task(status=status, wait=wait)), now=NOW)
        row = next(r for rows in m['sections'].values() for r in rows)
        assert 'wait_reason' not in row and 'wait_next_actor' not in row
        assert not any(d.startswith(('Waiting reason:', 'Next actor:', 'Next check:')) for d in row['details'])

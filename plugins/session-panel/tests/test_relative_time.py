"""Clock-only display changes must not invalidate an entire session bundle."""
import copy
import json

from test_feed import make, feedmod, detail

NOW = 1800000000


def test_clipping_never_loses_the_deadline_behind_a_long_location():
    rel = feedmod._model()._relative
    text = f'waiting {rel.relative(NOW - 600, "elapsed")} · {rel.relative(NOW + 60, "remaining")} left (until a fixed date)'
    encoded = rel.encode({'detail': rel.clip(text, 24)})
    parts = encoded['clock_text']['detail']
    assert [p['format'] for p in parts if isinstance(p, dict)] == ['elapsed', 'remaining']
    assert parts[-1] == ' left…'


def test_clock_only_two_minute_advance_has_no_changes_entry(monkeypatch):
    f, readers, pushed, clock = make()
    clock['now'] = NOW
    reducer = feedmod._model()
    monkeypatch.setattr(reducer, 'observation', lambda *args: {'state': 'running'})
    monkeypatch.setattr(feedmod, '_model', lambda: reducer)
    readers.links['S1'] = [
        {'id': 't_running', 'status': 'running', 'board': 'default', 'run_machine': 'worker',
         'run_model': 'example-model', 'current_run_started_at': NOW - 36 * 60, 'activity_at': NOW - 60},
        {'id': 't_wait', 'status': 'scheduled', 'board': 'default',
         'wait': {'waiting_on': 'the clock until 2027-01-16 12:00 UTC', 'waiting_since': NOW - 600}},
        {'id': 't_ready', 'status': 'ready', 'board': 'default', 'ready_since': NOW - 300},
    ]
    readers.finished.clear()
    readers.cards = {r['id']: detail(r['id'], r['status']) for r in readers.links['S1']}
    initial = f.changes(['S1'])
    cursor = initial['rev']
    before = copy.deepcopy(f.bundles['S1']['bundle']['model'])
    clock['now'] += 120
    f._publish({}, age_only=True)
    assert f.bundles['S1']['bundle']['model'] == before
    assert f.changes(['S1'], since=cursor, epoch=initial['epoch'])['bundles'] == {}
    assert 'min ago' not in json.dumps(before)
    assert before['sections']['running'][0]['clock_text']['detail'][-1] == {
        'timestamp': NOW - 36 * 60, 'format': 'elapsed'}
    readers.links['S1'][0]['title'] = 'Changed title'
    f._publish({'S1': f.compute('S1')})
    assert 'S1' in f.changes(['S1'], since=cursor, epoch=initial['epoch'])['bundles']


def test_wire_templates_preserve_static_strings_and_have_no_internal_tokens(monkeypatch):
    model = feedmod._model()
    b = {'computed_at': NOW, 'tasks': [{'id': 't_run', 'status': 'running',
         'started_at': NOW - 36 * 60, 'activity_at': NOW - 60, 'run_machine': 'worker'}]}
    monkeypatch.setattr(model, 'observation', lambda *args: {'state': 'running'})
    wire = model.reduce_panel(b, now=NOW, wire=True)
    row = wire['sections']['running'][0]
    assert isinstance(row['detail'], str)
    assert all(isinstance(s, str) for s in row['details'])
    assert row['clock_text']['details'][1][0] == 'started '
    assert '\\u001e' not in json.dumps(wire)

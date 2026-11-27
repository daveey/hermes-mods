"""Overdue visual attention is not human attention or execution permission."""
import copy
import pytest
pytest.importorskip('hermes_cli.kanban_triage_attention')
from test_model import model, task, bundle, human, canonical_outcome, NOW


def aged(age=601, *, remote=False, entry=None, **kw):
    row = task(status='triage', remote=remote, source='replica' if remote else 'live',
               block_kind='decision_pending', created_at=1, **kw)
    events = [{'kind': 'created', 'created_at': 1, 'payload': {'status': 'ready'}},
              {'kind': 'decision_retained', 'created_at': NOW - age if entry is None else entry},
              {'kind': 'decision_retained', 'created_at': NOW - 1}]
    return bundle(row, outcome_json=canonical_outcome('waiting'),
                  cards={row['id']: {'detail': {'task': dict(row), 'events': events}}})


@pytest.mark.parametrize('remote', [False, True])
@pytest.mark.parametrize('age,red', [(599, False), (600, False), (601, True), (6 * 86400, True)])
def test_triage_age_changes_attention_not_actor_or_authority(age, red, remote):
    b = aged(age, remote=remote)
    before = copy.deepcopy(b)
    result = model.reduce_panel(b, now=NOW, wire=True)
    row = result['sections']['stuck' if red else 'waiting'][0]
    assert (row.get('attention') == 'triage_overdue') is red
    assert 'agent' in row['detail']
    assert not result['sections']['needs_you']
    assert result['header']['status'] == b['outcome_json']['status']
    assert (result['header'].get('attention') == 'triage_overdue') is red
    assert b == before
    assert not any(a['kind'] == 'decide' for a in row['actions'])


@pytest.mark.parametrize('entry', [None, 'yesterday', -1, float('nan'), float('inf'), NOW + 20])
def test_unknown_entry_never_fabricates_age(entry):
    b = aged()
    b['cards']['t_abc123']['detail']['events'][1]['created_at'] = entry
    result = model.reduce_panel(b, now=NOW)
    assert not result['sections']['stuck']
    assert 'entry time unavailable' in result['sections']['waiting'][0]['detail']


def test_live_human_ask_is_red_before_threshold():
    b = aged(1, human_request=human())
    b['tasks'][0]['block_kind'] = 'needs_input'
    b['cards']['t_abc123']['detail']['task']['block_kind'] = 'needs_input'
    result = model.reduce_panel(b, now=NOW)
    assert len(result['sections']['needs_you']) == 1
    assert not result['sections']['stuck']


def test_wire_revision_only_changes_at_semantic_boundary_and_clears_on_exit():
    b = aged(599)
    first = model.reduce_panel(b, now=NOW, wire=True)
    boundary = model.reduce_panel(b, now=NOW + 1, wire=True)
    red = model.reduce_panel(b, now=NOW + 2, wire=True)
    assert first['revision'] == boundary['revision']
    assert first['revision'] != red['revision']
    assert red['revision'] == model.reduce_panel(b, now=NOW + 3, wire=True)['revision']
    b['tasks'][0]['status'] = 'ready'
    b['cards']['t_abc123']['detail']['task']['status'] = 'ready'
    assert 'attention' not in model.reduce_panel(b, now=NOW + 3)['header']


def test_feed_crosses_threshold_with_unchanged_adapters_and_no_reads():
    from test_feed import make, detail
    f, readers, _, clock = make()
    row = {'id': 't_aaaaaa1', 'status': 'triage', 'board': 'default', 'session_id': 'S1', 'block_kind': 'decision_pending'}
    readers.links['S1'] = [row]
    readers.cards[row['id']] = detail(row['id'], 'triage', events=[{'kind': 'decision_retained', 'created_at': 401}])
    f.changes(['S1'])
    reads = list(readers.reads)
    revision = f.rev
    clock['now'] = 1001
    f.beat()
    assert f.rev == revision
    clock['now'] = 1002
    f.beat()
    assert f.rev > revision
    held = f.bundles['S1']['bundle']
    assert held['model']['header']['attention'] == 'triage_overdue'
    assert held['computed_at'] == 1000
    assert readers.reads == reads
    revision = f.rev
    clock['now'] = 1003
    f.beat()
    assert f.rev == revision

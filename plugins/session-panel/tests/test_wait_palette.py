"""One wait colour category for a row box and the numbered waiting square.

User report: a stale CI wait painted an amber row box beside a blue waiting square. A known CI/agent/
dependency wait keeps its category when its source goes stale or unknown (`last_wait_kind`, colour only),
while live authority (`wait_kind`, ETA, header dot) is still withdrawn and the text says it is last known.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('panel_palette_fixtures', ROOT / 'tests' / 'test_model.py')
assert spec is not None and spec.loader is not None
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
model, NOW, bundle, task = f.model, f.NOW, f.bundle, f.task

CI = {'wait_state': 'external_running', 'waiting_on': 'ci example/repo PR #751', 'waiting_since': NOW - 300}


def waiting_pip(m):
    return next(p for p in m['header']['pips'] if p['section'] == 'waiting')


def category(item):
    return item.get('wait_kind') or item.get('last_wait_kind')


def ci_row(**kw):
    return task(status='scheduled', wait=dict(CI), **kw)


def test_fresh_ci_wait_row_and_pip_share_the_live_category():
    m = model.reduce_panel(bundle(ci_row()), now=NOW)
    row, pip = m['sections']['waiting'][0], waiting_pip(m)
    assert row['wait_kind'] == 'ci' and 'last_wait_kind' not in row
    assert pip['wait_kind'] == 'ci' and 'last_wait_kind' not in pip
    assert m['header']['wait_kind'] == 'ci'


def stale_cases():
    yield 'stale replica synced 120 s ago', bundle(ci_row(remote=True, source='replica', stale=True, synced_at=NOW - 120))
    yield 'stale replica not synced for 58 min', bundle(ci_row(remote=True, source='replica', stale=True, synced_at=NOW - 3480))
    yield 'owner read stale', bundle(ci_row(stale=True))
    yield 'partial owner read', bundle(ci_row(partial_source_unknown='origin_ledger_unavailable'))
    yield 'aged board data past the delivery window', bundle(ci_row(), computed_at=NOW - 3480)


def test_stale_or_unknown_ci_wait_keeps_its_category_on_row_and_pip_without_live_authority():
    for name, b in stale_cases():
        for wire in (False, True):
            m = model.reduce_panel(b, now=NOW, projector=f.project, wire=wire)
            [row] = m['sections']['waiting']
            pip = waiting_pip(m)
            assert 'wait_kind' not in row and 'wait_eta' not in row, name  # no live producer claim
            assert row['last_wait_kind'] == 'ci', name
            assert 'wait_kind' not in pip and pip['last_wait_kind'] == 'ci', name
            assert category(row) == category(pip), name
            assert 'wait_kind' not in m['header'], name  # the header dot never vouches for a stale producer
            assert row.get('evidence_state') == 'unknown', name


def test_recovery_restores_the_live_category():
    for name, b in stale_cases():
        stale = model.reduce_panel(b, now=NOW, projector=f.project)
        fresh = model.reduce_panel(bundle(ci_row()), now=NOW, projector=f.project)
        assert waiting_pip(stale)['last_wait_kind'] == 'ci', name
        assert waiting_pip(fresh)['wait_kind'] == 'ci' and 'last_wait_kind' not in waiting_pip(fresh), name
        assert 'last_wait_kind' not in fresh['sections']['waiting'][0], name


def test_mixed_waits_pip_is_amber_only_when_a_row_has_no_category():
    plain = task('t_plain', status='blocked')
    stale_ci = ci_row(tid='t_ci', stale=True)
    m = model.reduce_panel(bundle(stale_ci, plain), now=NOW, projector=f.project)
    rows = {r['id']: r for r in m['sections']['waiting']}
    assert rows['t_ci']['last_wait_kind'] == 'ci'
    assert category(rows['t_plain']) is None
    pip = waiting_pip(m)
    assert 'wait_kind' not in pip and 'last_wait_kind' not in pip  # amber, as the uncategorised row
    # Live CI beside a stale CI: one category for every box, carried as last-known on the pip.
    m = model.reduce_panel(bundle(ci_row(tid='t_live'), stale_ci), now=NOW, projector=f.project)
    assert {category(r) for r in m['sections']['waiting']} == {'ci'}
    assert waiting_pip(m) == {**waiting_pip(m), 'last_wait_kind': 'ci'} and 'wait_kind' not in waiting_pip(m)
    assert 'wait_kind' not in m['header']


def test_a_wait_with_no_known_category_stays_uncategorised():
    for flag in ({'stale': True}, {'partial_source_unknown': 'origin_ledger_unavailable'}):
        row = task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': 'something else'}, **flag)
        m = model.reduce_panel(bundle(row), now=NOW, projector=f.project)
        assert category(m['sections']['waiting'][0]) is None
        assert 'last_wait_kind' not in waiting_pip(m) and 'wait_kind' not in waiting_pip(m)


def stalled_cases():
    yield 'ci', bundle(task(status='scheduled', wait={**CI, 'wait_state': 'stalled'}))
    b = f.held_bundle(kind='spend_budget_triaged')
    b['cards']['t_abc123']['detail']['events'] = [
        {'kind': 'spend_budget_triaged', 'created_at': NOW - 3600, 'payload': {'limit': 'runs'}},
        {'kind': 'spend_review_escalated', 'created_at': NOW - 1900,
         'payload': {'episode': 'e', 'reason': 'Repeated wait.'}}]
    yield 'spend', b
    b = f.held_bundle()
    b['cards']['t_abc123']['detail']['events'][0]['created_at'] = NOW - 601
    yield 'triage', b


def test_stale_stalled_waits_do_not_inherit_a_blue_producer_category():
    import copy
    for name, b in stalled_cases():
        if name == 'triage' and not f.AGED:
            continue
        fresh = model.reduce_panel(b, now=NOW, projector=f.project)
        assert fresh['sections']['stuck'], name
        for flag in ({'stale': True}, {'partial_source_unknown': 'unavailable'},
                     {'stale': True, 'remote': True, 'source': 'replica', 'synced_at': NOW - 120}):
            stale = copy.deepcopy(b)
            stale['tasks'][0].update(flag)
            m = model.reduce_panel(stale, now=NOW, projector=f.project)
            [row] = m['sections']['waiting']
            assert category(row) is None, (name, flag, row)
            assert category(waiting_pip(m)) is None
            assert 'wait_eta' not in row and row['evidence_state'] == 'unknown'


def withdrawal_cases():
    # A contradictory observation is not the normal missing-run producer gap.
    yield 'unknown_ci', bundle(f.observed(ci_row(), state='unknown'), computed_at=NOW - 120), 'ci'
    yield 'aged_agent', {**f.held_bundle(status='blocked'), 'computed_at': NOW - 120}, 'agent'
    parent = task('t_abcdef0123456789', status='running')
    yield 'aged_card', bundle(task(status='scheduled', wait={'waiting_on': 'card t_abcdef0123456789'}, upstream=parent), computed_at=NOW - 400,
                             cards={'t_abcdef0123456789': {'detail': {'task': parent}}}), 'card'


def test_unknown_execution_and_aged_non_delivery_withdraw_live_authority_but_keep_category():
    for name, b, kind in withdrawal_cases():
        for wire in (False, True):
            m = model.reduce_panel(b, now=NOW, projector=f.project, wire=wire)
            [row] = m['sections']['waiting']
            assert row.get('last_wait_kind') == kind, (name, row)
            assert 'wait_kind' not in row and 'wait_eta' not in row
            assert row['evidence_state'] == 'unknown'
            assert waiting_pip(m).get('last_wait_kind') == kind
            assert 'wait_kind' not in m['header']
            if name == 'unknown_ci':
                assert row['detail'].startswith('status unknown (unknown)')


def test_identical_input_keeps_revision_and_category_change_moves_it():
    b = bundle(ci_row(stale=True))
    assert model.reduce_panel(b, now=NOW)['revision'] == model.reduce_panel(b, now=NOW)['revision']
    other = bundle(task(status='scheduled', stale=True, wait={'wait_state': 'waiting', 'waiting_on': 'something else'}))
    assert model.reduce_panel(b, now=NOW)['revision'] != model.reduce_panel(other, now=NOW)['revision']

"""Dependency outline contract, exercised through the real reducer."""
import copy
import random

from test_model import NOW, bundle, human, model, observed, project, task


def outline_case(edges, displayed=None, running=()):
    ids = set(edges) | {p for parents in edges.values() for p in parents}
    tasks = {i: task(i, status='running' if i in running else 'todo', title='Card ' + i) for i in sorted(ids)}
    cards = {i: {'detail': {'owner': 'remote', 'task': tasks[i],
                           'links': {'parents': edges.get(i, [])}}} for i in tasks}
    return bundle(*(observed(tasks[i]) if i in running else tasks[i] for i in tasks
                    if displayed is None or i in displayed), cards=cards)


def reduce(b, **kw):
    return model.reduce_panel(b, now=NOW, projector=project, **kw)


def rows(m):
    return {r['id']: r for section in m['sections'].values() for r in section}


def test_direct_multihop_cross_section_and_text():
    b = outline_case({'t_a': ['t_b'], 't_b': ['t_c']}, displayed=['t_a', 't_c'], running=['t_c'])
    b['tasks'][0]['wait'] = {'waiting_since': NOW - 120}
    m = reduce(b)
    r = rows(m)
    assert r['t_a']['parent'] == r['t_c']['key']
    assert r['t_a']['depth'] == 1
    assert 'waiting on:' not in r['t_a']['nested_detail']
    assert '2m' in r['t_a']['nested_detail']
    assert 'waiting on:' in r['t_a']['detail'], 'legacy flat clients retain the dependency'
    assert m['header']['counts']['running'] == 1
    assert m['header']['counts']['waiting'] == 1
    direct = rows(reduce(outline_case({'t_a': ['t_b']})))
    assert direct['t_a']['parent'] == direct['t_b']['key']


def test_nearest_displayed_parent_not_terminal_leaf():
    r = rows(reduce(outline_case({'t_a': ['t_b'], 't_b': ['t_c']})))
    assert r['t_a']['parent'] == r['t_b']['key']
    assert r['t_b']['parent'] == r['t_c']['key']
    # The leaf label is different from the visible immediate parent; retain it.
    assert 'Card t_c' in r['t_a']['detail']


def test_stuck_ready_and_human_rows_can_nest_without_losing_own_state():
    for status, wait, extra, section in [
        ('scheduled', {'wait_state': 'stalled'}, {}, 'stuck'),
        ('ready', {}, {}, 'ready'),
        ('blocked', {'wait_state': 'human'}, {'human_request': human()}, 'needs_you'),
    ]:
        b = outline_case({'t_a': ['t_b']}, running=['t_b'])
        b['tasks'][0].update(status=status, wait=wait, **extra)
        m = reduce(b)
        assert m['sections'][section][0]['parent'] == rows(m)['t_b']['key']
        assert m['header']['counts'][section] == 1


def test_wire_nested_detail_keeps_age_and_reason_but_flat_detail_keeps_parent():
    b = outline_case({'t_a': ['t_b']})
    b['tasks'][0]['wait'] = {'waiting_since': NOW - 120}
    b['cards']['t_b']['detail']['task']['block_reason'] = 'Keep warning'
    b['cards']['t_b']['detail']['task']['status'] = 'blocked'
    r = rows(reduce(b, wire=True))['t_a']
    assert 'waiting on:' in r['detail']
    assert r['nested_detail'] == 'Keep warning'
    assert r['clock_text']['nested_detail'][-1] == {'timestamp': NOW - 120, 'format': 'elapsed'}


def test_three_dependents_group_and_single_stays_flat():
    b = outline_case({'t_a': ['t_f'], 't_b': ['t_f'], 't_c': ['t_f']}, displayed=['t_a', 't_b', 't_c'])
    original = copy.deepcopy(b)
    m = reduce(b)
    assert b == original
    assert len(m['groups']) == 1
    group = m['groups'][0]
    assert group['kind'] == 'blocker'
    assert group['title'] == 'Card t_f'
    assert group['actions'][0]['target'] == {'id': 't_f', 'gateway': 'remote', 'profile': 'default', 'board': 'default'}
    assert {r['parent'] for r in rows(m).values()} == {group['key']}
    assert m['header']['counts']['waiting'] == 3
    assert m['header']['pips'] == [{'section': 'waiting', 'count': 3, 'label': 'waiting'}]
    assert m == reduce(b)
    single = reduce(outline_case({'t_a': ['t_f']}, displayed=['t_a']))
    assert single['groups'] == []
    assert rows(single)['t_a']['parent'] is None
    assert 'waiting on:' in rows(single)['t_a']['detail']


def test_cycle_and_depth_cap():
    m = reduce(outline_case({'t_a': ['t_b'], 't_b': ['t_a']}))
    assert all(r['parent'] is None for r in rows(m).values())
    edges = {f't_{i:x}': [f't_{i+1:x}'] for i in range(10)}
    m = reduce(outline_case(edges))
    assert max(r['depth'] for r in rows(m).values()) == 4
    assert len(rows(m)) == 11


def test_canonical_clock_does_not_acquire_parent():
    b = outline_case({'t_a': ['t_b']})
    b['tasks'][0]['wait'] = {'waiting_on': 'the clock until 2030-01-01', 'wait_state': 'waiting'}
    assert rows(reduce(b))['t_a']['parent'] is None


def test_owner_coordinates_never_match_bare_id():
    b = outline_case({'t_a': ['t_b']})
    b['tasks'][1] = {**b['tasks'][1], 'gateway': 'another-owner'}
    assert rows(reduce(b))['t_a']['parent'] is None


def test_unpublished_wait_keeps_all_hydrated_parents_not_only_upstream_hint():
    b = outline_case({'t_a': ['t_b', 't_c'], 't_c': ['t_d']})
    b['tasks'][0]['upstream'] = {'id': 't_b', 'title': 'Card t_b'}
    r = rows(reduce(b))
    assert r['t_a']['parent'] == r['t_b']['key'], 'nearest displayed ancestor'
    assert 'Card t_d' in r['t_a']['detail'], 'deepest label must consider all hydrated parents'


def test_randomized_exactly_once_counts_and_revision():
    rng = random.Random(401)
    for _ in range(60):
        edges = {f't_{i:x}': [f't_{rng.randrange(12):x}'] for i in range(12)}
        b = outline_case(edges)
        m = reduce(b)
        r = rows(m)
        assert len(r) == 12
        assert sum(m['header']['counts'].values()) == 12
        all_nodes = {v['key']: v for v in [*r.values(), *m['groups']]}
        for view in r.values():
            assert 0 <= view['depth'] <= 4
            path = set()
            while view['parent']:
                assert view['key'] not in path
                path.add(view['key'])
                parent = all_nodes[view['parent']]
                assert parent['depth'] + 1 == view['depth']
                view = parent
        assert m['revision'] == reduce(b)['revision']
        assert reduce(b, wire=True)['revision'] == reduce(b, wire=True)['revision']

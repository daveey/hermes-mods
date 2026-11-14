"""Multi-update carry overlaps immutable proofs without relaxing any gate."""
import importlib.util
from pathlib import Path
import threading

import pytest


def fixture(monkeypatch):
    spec = importlib.util.spec_from_file_location('carry_parallel_guard', Path(__file__).with_name('guard.py'))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    old, middle, head, base1, base2 = (c * 40 for c in '12345')
    def read(path, *args):
        if path.endswith(f'{old}...{head}'):
            return {'status': 'ahead', 'behind_by': 0, 'commits': [
                {'sha': middle, 'parents': [{'sha': old}, {'sha': base1}]},
                {'sha': head, 'parents': [{'sha': middle}, {'sha': base2}]}]}
        assert path.endswith('...state')
        return {'status': 'ahead', 'behind_by': 0}
    monkeypatch.setattr(g, '_gh_json', read)
    monkeypatch.setattr(g, '_gh_text', lambda *a, **k: 'same diff')
    monkeypatch.setattr(g, '_patch_id', lambda *a: 'same patch')
    monkeypatch.setattr(g, '_merge_tree_refusal', lambda *a: '')
    monkeypatch.setattr(g, '_git_merge_tree_refusal', lambda *a: '')
    return g, old, middle, head, base1, base2


def test_membership_reads_overlap_and_keep_deadline(monkeypatch):
    g, old, middle, head, base1, base2 = fixture(monkeypatch)
    read = g._gh_json
    barrier = threading.Barrier(2)
    seen = []
    def membership(path, *args):
        if path.endswith('...state'):
            seen.append(g._PREFLIGHT_DEADLINE.get())
            barrier.wait(timeout=2)
        return read(path, *args)
    monkeypatch.setattr(g, '_gh_json', membership)
    assert g.bounded_preflight(g._carry_one, head, old, False, 'example/repo', '.', {}) == (True, old)
    assert len(seen) == 2 and seen[0] is not None and seen[0] == seen[1]


def test_all_independent_carry_reads_overlap(monkeypatch):
    g, old, middle, head, base1, base2 = fixture(monkeypatch)
    read = g._gh_json
    barrier = threading.Barrier(8)
    def membership(path, *args):
        if path.endswith('...state'):
            barrier.wait(timeout=2)
        return read(path, *args)
    def proof(*args, **kwargs):
        barrier.wait(timeout=2)
        return ''
    monkeypatch.setattr(g, '_gh_json', membership)
    monkeypatch.setattr(g, '_gh_text', proof)
    monkeypatch.setattr(g, '_merge_tree_refusal', proof)
    monkeypatch.setattr(g, '_git_merge_tree_refusal', proof)
    assert g.bounded_preflight(g._carry_one, head, old, False, 'example/repo', '.', {}) == (True, old)


@pytest.mark.parametrize('failure', ['', 'prefilter', 'authoritative', 'unreadable', 'expired'])
def test_merge_proofs_overlap_join_and_fail_closed(monkeypatch, failure):
    g, old, middle, head, base1, base2 = fixture(monkeypatch)
    barrier = threading.Barrier(4)
    seen, finished = [], []
    def proof(kind, merge):
        seen.append((kind, merge, g._PREFLIGHT_DEADLINE.get()))
        barrier.wait(timeout=2)
        finished.append((kind, merge))
        if merge == middle:
            if failure == 'unreadable' and kind == 'prefilter':
                raise g._Unreadable('unreadable merge')
            if failure == 'expired' and kind == 'authoritative':
                raise g._PreflightExpired('deadline exceeded in merge proof')
            if failure == kind:
                return kind + ' rejected'
        return ''
    monkeypatch.setattr(g, '_merge_tree_refusal', lambda merge, *a: proof('prefilter', merge))
    monkeypatch.setattr(g, '_git_merge_tree_refusal', lambda merge, *a: proof('authoritative', merge))
    result = g.bounded_preflight(g._carry_one, head, old, False, 'example/repo', '.', {})
    assert sorted(finished) == sorted((k, m) for k in ('prefilter', 'authoritative') for m in (middle, head))
    assert len({d for _, _, d in seen}) == 1 and seen[0][2] is not None
    if not failure:
        assert result == (True, old)
    elif failure == 'expired':
        assert result == 'deadline exceeded in merge proof'
    else:
        assert result[0] is False and failure in result[1]


@pytest.mark.parametrize('bounded', [False, True])
def test_drift_expiry_keeps_actual_stage(monkeypatch, bounded):
    import json
    from types import SimpleNamespace
    g, old, middle, head, base1, base2 = fixture(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(g.time, 'monotonic', lambda: clock[0])
    data = dict(number=12, url='https://github.com/example/repo/pull/12', state='OPEN',
                baseRefName='state', headRefOid=head, statusCheckRollup=[
                    dict(__typename='CheckRun', status='COMPLETED', conclusion='SUCCESS')])
    monkeypatch.setattr(g, '_run_probe', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(data)))
    monkeypatch.setattr(g, '_review_reply_refusal', lambda *a: None)
    monkeypatch.setattr(g, '_approval_refusal', lambda *a: None)
    monkeypatch.setattr(g, '_turn_hold_read_only', lambda *a: None)
    def drift(*args):
        clock[0] += g.PREFLIGHT_TIMEOUT + 1
        g._remaining()
    monkeypatch.setattr(g, '_base_drift_refusal', drift)
    words = ['pr', 'merge', '12', '-R', 'example/repo', '--rebase', '--match-head-commit', head]
    args = (g._state_merge, words, 'example/repo', '.', {})
    result = g.bounded_preflight(*args) if bounded else args[0](*args[1:])
    assert 'deadline exceeded during drift check' in result
    assert 'command inspection' not in result
    assert g._PREFLIGHT_DEADLINE.get() is None


def test_drift_attribution_reads_overlap(monkeypatch):
    import json
    from types import SimpleNamespace
    g, old, middle, head, base1, base2 = fixture(monkeypatch)
    barrier = threading.Barrier(3)
    def run(*a, **k):
        barrier.wait(timeout=2)
        return SimpleNamespace(returncode=0, stdout=json.dumps([{'sha': head}]))
    monkeypatch.setattr(g, '_run_probe', run)
    result = g.bounded_preflight(g._attribute_hits, 'example/repo', ['a', 'b', 'c'],
                                {head: {'sha': head}}, lambda c: 'subject', '.', {})
    assert result == '; '.join(f'{n}: {head[:10]} subject' for n in ['a', 'b', 'c'])

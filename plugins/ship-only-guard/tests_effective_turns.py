"""Pure effective-holder selection shared with read-only urgency observers."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('effective_turn_guard', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def ticket(n, files, acquired=None):
    rec = {'pr': n, 'files': files, 'expires': 10000, 'head': 'a' * 40}
    if acquired is not None:
        rec['acquired'] = acquired
    return (f'refs/land-turn/pr-{n}', rec)


def selected(turns, eligible=lambda ref, rec: True, check=lambda: None):
    return [rec['pr'] for _, rec in g.effective_turns(turns, eligible, check)]


def test_order_and_nontransitive_waiters_do_not_reserve_area():
    turns = [ticket(3, ['y'], 3), ticket(2, ['x', 'y'], 2), ticket(1, ['x'], 1)]
    seen = []
    assert selected(turns, lambda ref, rec: seen.append(rec['pr']) or True) == [1, 3]
    assert seen == [1, 3]


def test_yielded_or_closed_holder_does_not_block_waiter():
    assert selected([ticket(1, ['x'], 1), ticket(2, ['x'], 2)],
                    lambda ref, rec: rec['pr'] != 1) == [2]


def test_legacy_acquisition_and_equal_time_pr_order():
    acquired = 10000 - g._TURN_TTL
    assert selected([ticket(3, ['x'], acquired), ticket(2, ['x'])]) == [2]


def test_owner_metadata_never_filters_blockers():
    first = ticket(1, ['x'], 1)
    second = ticket(2, ['x'], 2)
    second[1].update(owner_task='t_target', owner_board='default')
    assert selected([second, first]) == [1]
    first[1].update(owner_task='t_foreign', owner_board='other')
    assert selected([second, first]) == [1]


def test_eligibility_exception_never_promotes_waiter():
    def unavailable(ref, rec):
        raise RuntimeError('missing evidence')
    with pytest.raises(RuntimeError, match='missing evidence'):
        selected([ticket(1, ['x'], 1), ticket(2, ['x'], 2)], unavailable)


def test_deadline_checked_after_eligibility_before_yield():
    calls = []
    def check():
        calls.append('check')
        if len(calls) == 3:
            raise TimeoutError('late result')
    def eligible(ref, rec):
        calls.append('read')
        return True
    with pytest.raises(TimeoutError, match='late result'):
        selected([ticket(1, ['x'], 1)], eligible, check)
    assert calls == ['check', 'read', 'check']


def test_selector_is_lazy_after_first_holder():
    seen = []
    gen = g.effective_turns([ticket(2, ['y'], 2), ticket(1, ['x'], 1)],
                            lambda ref, rec: seen.append(rec['pr']) or True, lambda: None)
    assert next(gen)[1]['pr'] == 1
    assert seen == [1]
    gen.close()

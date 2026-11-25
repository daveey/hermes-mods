"""A decision-only read cannot settle an unpublished runtime capture."""
import pytest

from test_process_refresh import setup


@pytest.mark.parametrize('exiting', [False, True])
def test_superseded_runtime_capture_retries_birth_and_exit(exiting):
    f, r, clock, calls = setup()
    if exiting:
        f.refresh_decisions()
        clock['now'] += 46
    baseline = f.bundles['S1']['bundle']['computed_at']
    capture = r.runtime_inputs

    def interleaved(*args):
        fresh = capture(*args)
        if exiting:
            fresh['processes'] = []
        # The newer decision-only result carries the old process inventory.
        f.refresh_decision('S1', readers=r)
        return fresh

    r.runtime_inputs = interleaved
    f.refresh_decisions()
    assert bool(f.bundles['S1']['bundle']['processes']) is exiting
    assert f._runtime_retry_at is not None
    assert f._runtime_retry_at <= clock['now'] + 45

    def recovered(*args):
        fresh = capture(*args)
        if exiting:
            fresh['processes'] = []
        return fresh

    r.runtime_inputs = recovered
    clock['now'] += 2
    f.refresh_decisions()
    held = f.bundles['S1']['bundle']
    assert bool(held['processes']) is not exiting
    assert held['computed_at'] == baseline
    if exiting:
        assert f._runtime_retry_at is None
    else:
        assert held['processes'][0]['title'] == 'sleep 600'
        assert f._runtime_retry_at == clock['now'] + 45
    captures = len(calls)
    f.refresh_decisions()
    assert len(calls) == captures  # settlement disarms immediate retry


@pytest.mark.parametrize('exiting', [False, True])
def test_late_decision_publication_cannot_restore_old_process_inventory(exiting):
    f, r, clock, calls = setup()
    if exiting:
        f.refresh_decisions()
        clock['now'] += 46
    capture = r.runtime_inputs
    old_read = r.decision_inputs
    inside = False

    def decision(*args):
        nonlocal inside
        if not inside:
            inside = True
            # This interactive read has already captured its old base. A real
            # background capture now publishes birth/exit before it returns.
            f.refresh_decisions()
        return old_read(*args)

    def runtime(*args):
        fresh = capture(*args)
        if exiting:
            fresh['processes'] = []
        return fresh

    r.runtime_inputs = runtime
    r.decision_inputs = decision
    f.refresh_decision('S1', readers=r)
    assert bool(f.bundles['S1']['bundle']['processes']) is not exiting


@pytest.mark.parametrize('retired', ['forgotten', 'lineage'])
def test_retired_runtime_capture_does_not_arm_a_ghost_retry(retired):
    f, r, clock, calls = setup()
    capture = r.runtime_inputs

    def runtime(*args):
        fresh = capture(*args)
        if retired == 'forgotten':
            f._forget('S1')
        else:
            held = f.bundles['S1']
            held['bundle'] = {**held['bundle'], 'lineage': ['S1', 'new-parent']}
        return fresh

    r.runtime_inputs = runtime
    f.refresh_decisions()
    assert f._runtime_retry_at is None
    if retired == 'forgotten':
        assert 'S1' not in f.bundles
    else:
        assert f.bundles['S1']['bundle']['processes'] == []

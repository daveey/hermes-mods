from test_model import canonical_outcome
import threading

from test_feed import make


def test_interactive_refresh_wins_over_inflight_sweep_without_changing_board_trust():
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda *args: {'outcome_json': canonical_outcome('decision'), 'requests': []}
    f.changes(['S1'])
    f._carried.add('S1'); f._reissue.add('S1')
    before = f.bundles['S1']['bundle']['computed_at']
    started, release = threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        assert release.wait(5)
        return {'outcome_json': canonical_outcome('waiting'), 'requests': []}
    readers.decision_inputs = slow
    worker = threading.Thread(target=f.refresh_decisions)
    worker.start()
    try:
        assert started.wait(5)
        class Interactive:
            def decision_inputs(self, session, lineage):
                assert session == 'S1'
                return {'outcome_json': canonical_outcome('done'), 'requests': []}
        result = f.refresh_decision('S1', readers=Interactive())
        assert result['bundle']['model']['header']['state'] == 'done'
        assert result['bundle']['computed_at'] == before
        assert 'S1' in f._carried and 'S1' in f._reissue
    finally:
        release.set(); worker.join(5)
    assert not worker.is_alive()
    assert f.bundles['S1']['bundle']['model']['header']['state'] == 'done'


def test_interactive_refresh_refuses_partial_inputs_and_recovers():
    import pytest
    f, readers, pushed, clock = make()
    f.changes(['S1'])
    before = f.rev
    readers.decision_inputs = lambda *args: {'requests': []}
    with pytest.raises(ValueError, match='incomplete'):
        f.refresh_decision('S1', readers=readers)
    assert f.rev == before
    assert 'S1' in f._decision_failures
    assert f._decision_retry_at is not None
    clock['now'] = f._decision_retry_at
    f.beat()
    assert f._decide.is_set()
    readers.decision_inputs = lambda *args: {'outcome_json': {}, 'requests': []}
    assert f.refresh_decision('S1', readers=readers)['bundle']['session'] == 'S1'
    assert 'S1' not in f._decision_failures
    with pytest.raises(ValueError, match='not hydrated'):
        f.refresh_decision('never-watched', readers=readers)


def test_recorded_and_delivered_request_reaches_model_without_board_reads():
    from test_model_audit import answered
    f, readers, pushed, clock = make()
    req = answered()
    from datetime import datetime
    clock['now'] = datetime.fromisoformat(req['resolution']['recordedAt']).timestamp() + 60
    readers.panel_inputs = lambda session, lineage: {'outcome_json': {}, 'requests': [{**req, 'resolution': None}]}
    f.changes(['S1'])
    assert f.bundles['S1']['bundle']['model']['sections']['needs_you']
    readers.decision_inputs = lambda session, lineage: {'requests': [req], 'outcome_json': {}}
    readers.card = lambda *args: (_ for _ in ()).throw(AssertionError('no hydration'))
    assert f.refresh_decisions() == 1
    model = pushed[-1]['bundles']['S1']['model']
    assert not model['sections']['needs_you']
    assert model['sections']['waiting'][-1]['decision']['text'] == 'Done · delivers when this turn ends'
    req = {**req, 'state': 'resolved'}
    assert f.refresh_decisions() == 1
    assert not any(r.get('decision') for rows in pushed[-1]['bundles']['S1']['model']['sections'].values() for r in rows)


def test_canonical_lane_does_not_vouch_for_restored_board_evidence():
    f, readers, pushed, clock = make()
    f.changes(['S1'])
    f._carried.add('S1')
    f._reissue.add('S1')
    readers.decision_inputs = lambda session, lineage: {'outcome_json': canonical_outcome('done'), 'requests': []}
    f.refresh_decisions()
    assert 'S1' in f._carried and 'S1' in f._reissue
    assert 'S1' not in pushed[-1]['fresh']


def test_fast_read_is_discarded_on_changed_lineage_or_forgotten_interest():
    for forget in (False, True):
        f, readers, pushed, clock = make()
        readers.panel_inputs = lambda session, lineage: {'outcome_json': canonical_outcome('waiting'), 'requests': []}
        f.changes(['S1'])
        def changed(session, lineage):
            if forget:
                with f.lock:
                    f._forget(session)
                f.watch([session])
            else:
                readers.lineages['S1'] = ['S1', 'NEW']
                f._publish({'S1': f.compute('S1')})
            return {'outcome_json': canonical_outcome('done'), 'requests': []}
        readers.decision_inputs = changed
        assert f.refresh_decisions() == 0
        if forget:
            assert 'S1' not in f.bundles
        else:
            assert f.bundles['S1']['bundle']['model']['header']['state'] == 'waiting'



def test_canonical_refresh_publishes_while_unrelated_board_read_is_blocked():
    f, readers, pushed, clock = make()
    state = {'outcome_json': canonical_outcome('waiting'), 'requests': []}
    readers.panel_inputs = lambda session, lineage: dict(state)
    readers.decision_inputs = lambda session, lineage: dict(state)
    f.changes(['S1', 'S2'])
    started, release = threading.Event(), threading.Event()
    original = readers.card
    def blocked(cid, board):
        started.set()
        assert release.wait(5)
        return original(cid, board)
    readers.card = blocked
    worker = threading.Thread(target=f.tick)
    worker.start()
    try:
        assert started.wait(5)
        state['outcome_json'] = canonical_outcome('done')
        before = f.bundles['S2']['bundle']['computed_at']
        clock['now'] += 10
        assert f.refresh_decisions() == 2
        assert pushed[-1]['bundles']['S2']['model']['header']['state'] == 'done'
        assert f.bundles['S2']['bundle']['computed_at'] == before, 'canonical refresh is not fresh board evidence'
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert f.bundles['S2']['bundle']['model']['header']['state'] == 'done'


def test_old_full_read_cannot_restore_an_undecided_request_after_fast_refresh():
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda session, lineage: {'outcome_json': canonical_outcome('decision'), 'requests': []}
    f.changes(['S1'])
    old = f.compute('S1')
    readers.decision_inputs = lambda session, lineage: {'outcome_json': canonical_outcome('done'), 'requests': []}
    f.refresh_decisions()
    f._publish({'S1': old})
    assert f.bundles['S1']['bundle']['model']['header']['state'] == 'done'


def test_fast_read_racing_newer_board_read_keeps_new_board_and_canonical_state():
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda session, lineage: {'outcome_json': canonical_outcome('waiting'), 'requests': []}
    f.changes(['S1'])
    started, release = threading.Event(), threading.Event()
    def delayed(session, lineage):
        started.set()
        assert release.wait(5)
        return {'outcome_json': canonical_outcome('done'), 'requests': []}
    readers.decision_inputs = delayed
    worker = threading.Thread(target=f.refresh_decisions)
    worker.start()
    try:
        assert started.wait(5)
        readers.links['S1'] = []
        fresh = f.compute('S1')
        f._publish({'S1': fresh})
    finally:
        release.set()
        worker.join(5)
    held = f.bundles['S1']['bundle']
    assert held['tasks'] == [], 'fast refresh cannot restore old board rows'
    assert held['model']['header']['state'] == 'waiting', 'a read started before the newer canonical read cannot win'


def test_fast_input_error_is_retried_without_board_stamps_and_not_vouched():
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda session, lineage: {'outcome_json': canonical_outcome('waiting'), 'requests': []}
    f.changes(['S1'])
    clock['now'] += 21
    f._publish({}, heartbeat=True)
    assert 'S1' in pushed[-1]['fresh'], 'positive freshness control before canonical failure'
    def broken(session, lineage):
        raise RuntimeError('canonical reader unavailable')
    readers.decision_inputs = broken
    clock['now'] += 21
    f.refresh_decisions()
    f._publish({}, heartbeat=True)
    assert 'S1' not in pushed[-1]['fresh']
    readers.decision_inputs = lambda session, lineage: {'outcome_json': canonical_outcome('done'), 'requests': []}
    assert f.refresh_decisions() == 1
    assert f.bundles['S1']['bundle']['model']['header']['state'] == 'done'
    assert 'S1' not in f._decision_failures
    assert 'S1' in pushed[-1]['fresh'], 'successful retry restores freshness, not just presentation'


def test_canonical_sequence_survives_resume_and_restore_without_churn():
    from test_feed import feedmod
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda session, lineage: {'outcome_json': {}, 'requests': []}
    readers.decision_inputs = readers.panel_inputs
    f.changes(['S1'])
    for _ in range(10):
        assert f.refresh_decisions() == 0
    old_seq = f.bundles['S1']['bundle']['canonical_seq']
    snap = f.snapshot()
    resumed = feedmod.Feed(readers, emit=pushed.append, clock=f.clock).resume(f)
    restored = feedmod.Feed(readers, emit=pushed.append, clock=f.clock).restore(snap)
    for current in (resumed, restored):
        current.refresh_decisions()
        assert current.bundles['S1']['bundle']['canonical_seq'] > old_seq
        assert 'S1' in current._carried
    assert f.refresh_decisions() == 0


def test_canonical_lineage_fence_even_when_board_sequences_are_equal():
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda *args: {'outcome_json': {}, 'requests': []}
    f.changes(['S1'])
    base = f.bundles['S1']['bundle']
    raced = {**base, 'lineage': ['S1', 'obsolete-parent'],
             'outcome_json': canonical_outcome('done'), 'canonical_seq': base['canonical_seq'] + 1}
    before = f.rev
    assert f._publish({'S1': raced}, canonical_only=True) == {}
    assert f.rev == before and f.bundles['S1']['bundle'] is base


def test_duplicate_joined_board_result_does_not_reissue_restored_evidence():
    f, readers, pushed, clock = make()
    f.changes(['S1'])  # legacy snapshot without canonical sequence
    base = f.bundles['S1']['bundle']
    f._carried.add('S1')
    f._reissue.add('S1')
    before = f.rev
    assert f._publish({'S1': dict(base)}) == {}
    assert f.rev == before
    assert 'S1' in f._carried and 'S1' in f._reissue


def test_unchanged_canonical_data_never_reduces_or_pushes(monkeypatch):
    from test_feed import feedmod
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda *args: {'outcome_json': {}, 'requests': []}
    readers.decision_inputs = readers.panel_inputs
    f.changes(['S1'])
    before, model = f.rev, f.bundles['S1']['bundle']['model']
    def forbidden():
        raise AssertionError('unchanged canonical inputs must not re-reduce')
    monkeypatch.setattr(feedmod, '_model', forbidden)
    assert f.refresh_decisions() == 0
    assert not f._decision_failures, 'an exception swallowed by refresh is not a successful no-op'
    assert f.rev == before and f.bundles['S1']['bundle']['model'] is model


def test_merged_reduction_uses_current_clock_not_board_snapshot(monkeypatch):
    from test_feed import feedmod
    f, readers, pushed, clock = make()
    readers.panel_inputs = lambda *args: {'outcome_json': {}, 'requests': []}
    f.changes(['S1'])
    old_clock = f.bundles['S1']['bundle']['computed_at']
    clock['now'] += 90
    readers.decision_inputs = lambda *args: {'outcome_json': canonical_outcome('done'), 'requests': []}
    real_model, seen = feedmod._model(), []
    class Reducer:
        def reduce_panel(self, bundle, *, now, wire=False):
            seen.append(now)
            return real_model.reduce_panel(bundle, now=now, wire=wire)
    monkeypatch.setattr(feedmod, '_model', Reducer)
    assert f.refresh_decisions() == 1
    assert seen == [clock['now']] and seen != [old_clock]
    assert f.bundles['S1']['bundle']['computed_at'] == old_clock


def test_additive_model_and_request_outcome_invalidation_without_card_events():
    f, readers, pushed, clock = make()
    state = canonical_outcome('waiting')
    readers.panel_inputs = lambda session, lineage: {'outcome_json': dict(state)}
    readers.panel_stamp = lambda session: state['kind']
    first = f.changes(['S1'])['bundles']['S1']
    assert all(k in first for k in ('tasks', 'done', 'cards', 'lineage', 'model'))
    assert first['model']['header']['state'] == 'waiting'
    f.tick()  # establish stamps
    state.update(canonical_outcome('decision', revision=10))
    assert f.tick() == 1  # unchanged board stamp and no task event
    assert pushed[-1]['bundles']['S1']['model']['header']['state'] == 'decision'
    assert f.tick() == 0

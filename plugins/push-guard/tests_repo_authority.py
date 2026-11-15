"""Installed authority integration (storage reads isolated, validation real)."""
from pathlib import Path
import runpy
import pytest
from tests_repo_landing import configured, REPO
from tests_landing import GitHub, HEAD, land

INSTALLED = Path.home() / '.hermes/lib/core-tools/hermes-core-wt-land'


@pytest.mark.skipif(not INSTALLED.is_file(), reason='installed authoritative validator absent')
@pytest.mark.parametrize('case', ['accept', 'self-session', 'in-run', 'stale', 'wrong-pr',
                                  'rejected', 'unreadable', 'head-move'])
def test_authoritative_validator_through_land(configured, monkeypatch, case):
    real = runpy.run_path(str(INSTALLED), run_name='review_validator_fixture')
    g = real['review_gate'].__globals__
    md = {'verdict': 'approve', 'reviewed_sha': HEAD,
          'reviewed_pr': f'https://github.com/{REPO}/pull/7'}
    record = {'task': {'id': 'review', 'status': 'done'},
              'runs': [{'id': 1, 'status': 'done', 'outcome': 'completed', 'ended_at': 1767312000,
                        'metadata': md}],
              'events': [{'kind': 'completed', 'run_id': 1},
                         {'kind': 'owner_admitted', 'run_id': 1,
                          'payload': {'session_id': 'review-session'}}]}
    if case == 'self-session': record['events'][1]['payload']['session_id'] = 'implementation-session'
    if case == 'in-run': record['task']['status'] = 'running'
    if case == 'stale': md['reviewed_sha'] = 'c' * 40
    if case == 'wrong-pr': md['reviewed_pr'] = f'https://github.com/{REPO}/pull/8'
    if case == 'rejected': md['verdict'] = 'request_changes'

    def unavailable(*args, **kwargs):
        raise real['ReviewRefused']('unreadable authoritative evidence')

    # Stub only storage/custody reads; retain review_gate, kanban_approval and
    # _review_verdict, including completed-run/event/session/PR/head validation.
    g['kanban_show'] = unavailable if case == 'unreadable' else lambda *a: record
    g['landing_sessions'] = lambda *a: ({'implementation'}, {'implementation-session'})
    g['board_landing_sessions'] = lambda *a, **k: {'implementation-session'}
    g['board_cross_check'] = lambda *a: None
    g['board_review_record'] = unavailable
    monkeypatch.setattr(land.runpy, 'run_path', lambda *a, **k: {'review_gate': real['review_gate']})
    monkeypatch.setattr(land.Path, 'home', lambda: configured)
    marker = configured / '.hermes/lib/core-tools/hermes-core-wt-land'
    marker.parent.mkdir(parents=True)
    marker.write_text('# fixture location')
    api = GitHub()
    api.pr['base']['repo']['full_name'] = REPO
    api.reviews = []
    if case == 'head-move': api.move = True
    if case == 'accept':
        result = land.land(7, HEAD, 'review', 'implementation', read=api, repo=REPO)
        assert result['review']['source'] == 'kanban' and len(api.writes) == 1
    else:
        with pytest.raises((land.Refused, real['ReviewRefused'])):
            land.land(7, HEAD, 'review', 'implementation', read=api, repo=REPO)
        assert not api.writes

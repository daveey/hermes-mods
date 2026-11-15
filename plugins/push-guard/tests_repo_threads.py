"""Approval cannot erase unresolved findings or an unreadable thread inventory."""
import pytest
from tests_repo_landing import configured, REPO
from tests_landing import GitHub, HEAD, land


@pytest.mark.parametrize('case', ['resolved', 'open', 'unreadable', 'errors', 'page-two', 'bad-cursor'])
def test_threads_before_merge(configured, case):
    api = GitHub()
    api.pr['base']['repo']['full_name'] = REPO
    calls = []
    def read(*args):
        if args[1] != 'graphql':
            return api(*args)
        calls.append(args)
        assert f'owner={REPO.split("/")[0]}' in args
        assert f'name={REPO.split("/")[1]}' in args
        if case == 'unreadable': return {}
        if case == 'errors': return {'errors': [{'message': 'denied'}]}
        more = case in ('page-two', 'bad-cursor') and len(calls) == 1
        nodes = [{'isResolved': case != 'open' and not (case == 'page-two' and len(calls) == 2)}]
        return {'data': {'repository': {'pullRequest': {'reviewThreads': {
            'nodes': nodes, 'pageInfo': {'hasNextPage': more,
                                       'endCursor': None if case == 'bad-cursor' else 'next'}}}}}}
    if case == 'resolved':
        assert land.land(7, HEAD, read=read, repo=REPO)['merged']
    else:
        with pytest.raises(land.Refused): land.land(7, HEAD, read=read, repo=REPO)
        assert not api.writes
    if case == 'page-two':
        assert len(calls) == 2 and 'cursor=next' in calls[-1]

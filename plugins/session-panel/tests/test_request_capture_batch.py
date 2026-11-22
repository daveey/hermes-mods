"""Reader composition reuses one canonical capture without sharing mutable rows."""
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


def reader(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
    spec = importlib.util.spec_from_file_location('_request_batch_api', path)
    import sys
    assert spec is not None and spec.loader is not None
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    r = api.CoreReaders.__new__(api.CoreReaders)
    r._local = threading.local()
    r.state_db = lambda: tmp_path / 'state.db'
    r.lineage = lambda sid: [sid, 'parent']
    return r


def test_request_capture_once_per_batch_and_new_pass_refreshes(tmp_path):
    r = reader(tmp_path)
    calls = []

    def capture(home, scopes):
        calls.append(scopes)
        return {sid: [{'title': str(len(calls))}] for sid in scopes}

    r._user_step_api = lambda: SimpleNamespace(panel_requests_many=capture)
    for version in ('1', '2'):
        with r.batch(['left', 'right']):
            first = r.user_step_requests('left', ['left', 'parent'])
            assert first == [{'title': version}]
            first[0]['title'] = 'mutated'
            assert r.user_step_requests('left', ['left', 'parent']) == [{'title': version}]
            assert r.user_step_requests('right', ['right', 'parent']) == [{'title': version}]
    assert len(calls) == 2
    assert calls[0] == {'left': ['left', 'parent'], 'right': ['right', 'parent']}


def test_failed_capture_is_not_certified_or_retried_per_session(tmp_path):
    r = reader(tmp_path)
    calls = []

    def capture(*args):
        calls.append(1)
        raise RuntimeError('source unavailable')

    r._user_step_api = lambda: SimpleNamespace(panel_requests_many=capture)
    with r.batch(['left', 'right']):
        for sid in ('left', 'right'):
            with pytest.raises(RuntimeError, match='source unavailable'):
                r.user_step_requests(sid, [sid, 'parent'])
    assert len(calls) == 1
    with r.batch(['left']):
        with pytest.raises(RuntimeError, match='source unavailable'):
            r.user_step_requests('left', ['left', 'parent'])
    assert len(calls) == 2

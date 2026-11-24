"""Completion grace uses producer completion, never the age of the park."""
import pytest
from sr_helpers import load, PLUGIN
pytest.importorskip('hermes_cli.kanban_db')
from test_patterns import env, F

@pytest.mark.parametrize('age', [0, 211, 438, 599, -1])
def test_completed_producer_gets_two_watcher_intervals(env, monkeypatch, age):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {
        'state': 'completed', 'happened': True, 'happened_at': env.now-age})
    tid = F.long_wait(env.conn, env.now, kind='ci', waited_s=10000)
    from test_patterns import one
    assert env.findings(rule='BS-long-wait', card=tid) == []

@pytest.mark.parametrize('age', [600, 601, 3600])
def test_unwoken_completion_after_grace_is_still_detected(env, monkeypatch, age):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {
        'state': 'completed', 'happened': True, 'happened_at': env.now-age})
    from test_patterns import one
    hit = one(env, 'BS-long-wait', F.long_wait(env.conn, env.now, kind='ci', waited_s=10000))
    assert hit['variant'] == 'event_happened'

def test_completion_without_timestamp_is_not_invented(env, monkeypatch):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'happened':True})
    from test_patterns import one
    tid = F.long_wait(env.conn, env.now, kind='ci', waited_s=10000)
    assert env.findings(rule='BS-long-wait', card=tid) == []

def test_producer_requests_timestamp_and_exposes_epoch():
    import json, subprocess
    p = load('_grace_producer', PLUGIN/'producer.py')
    def runner(argv, **kw):
        assert 'updatedAt' in argv[-1]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({
            'status':'completed', 'updatedAt':'2026-01-01T00:00:00Z'}))
    assert p.read_github('ci', 'https://github.com/example/project/actions/runs/123', runner)['happened_at'] == 1767225600

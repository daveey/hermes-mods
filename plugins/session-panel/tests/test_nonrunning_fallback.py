"""Core-free public renderer fallback must not invent worker-capacity evidence."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('nonrunning_fallback_model', Path(__file__).resolve().parents[1] / 'model.py')
assert spec is not None and spec.loader is not None
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
NOW = 1795281000


def test_absent_reason_cannot_be_rendered_as_worker_wait():
    value = panel._ready({'status': 'ready', 'ready_since': NOW - 120}, {}, NOW)
    assert value == 'ready · execution reason unknown'


def test_stale_replica_keeps_sync_age_without_inventing_capacity():
    row = {'id': 't_fixture', 'gateway': 'remote', 'profile': 'default', 'board': 'default',
           'title': 'Historical ready report', 'status': 'ready', 'remote': True,
           'source': 'replica', 'stale': True, 'synced_at': NOW - 120}
    bundle = {'tasks': [row], 'done': [], 'cards': {}, 'computed_at': NOW, 'read_at': NOW}
    model = panel.reduce_panel(bundle, now=NOW)
    view = model['sections']['ready'][0]
    assert 'execution reason unknown' in view['detail']
    assert 'synced 120s ago' in view['detail']
    assert 'for a worker' not in str(model)
    assert not model['sections']['needs_you'] and not model['sections']['stuck']


def test_explicit_queue_note_is_preserved_without_generic_worker_inference():
    assert panel._ready({'status': 'ready'}, {'queue_note': 'observed profile cap'}, NOW) == 'ready · observed profile cap'


@pytest.mark.parametrize('state', ['todo', 'scheduled', 'blocked', 'triage', 'review'])
def test_unexplained_parked_status_does_not_invent_parent_promotion_or_review_activity(state):
    text = panel._parked({'status': state}, '')
    assert 'execution reason unknown' in text
    assert 'parent' not in text and 'promoted' not in text and 'waiting for review' not in text


@pytest.mark.parametrize('state', ['todo', 'review', 'triage'])
def test_real_reducer_unexplained_lifecycle_preserves_unknown_actor(state):
    row = {'id': 't_fixture', 'gateway': 'local', 'profile': 'default', 'board': 'default',
           'title': 'Unexplained lifecycle', 'status': state}
    model = panel.reduce_panel({'tasks': [row], 'done': [], 'cards': {},
                               'computed_at': NOW, 'read_at': NOW}, now=NOW)
    views = [v for section in model['sections'].values() for v in section]
    view = next(v for v in views if v.get('id') == 't_fixture')
    assert 'execution reason unknown' in view['detail']
    assert 'this chat' not in view['detail']
    assert not model['sections']['needs_you']


def test_explicit_upstream_and_published_wait_remain_visible():
    assert panel._parked({'status': 'todo', 'upstream': {'id': 't_parent'}}, '') == 'waiting on parent card'
    assert panel._parked({'status': 'scheduled'}, 'cooldown until the recorded clock') == 'cooldown until the recorded clock'

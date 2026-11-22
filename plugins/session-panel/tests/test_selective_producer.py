"""The source producer reconciles only the chats whose own inputs moved, and the feed's timing is readable."""
import json
import time

from test_feed import make

WATCHED = [f'C{i}' for i in range(37)]


def setup():
    f, r, pushed, t = make()
    source = {'version': 1}
    calls = []
    r.reconcile_scoped = True
    r.reconciliation_stamp = lambda: source['version']
    r.reconcile_sources = lambda sessions: calls.append(list(sessions)) or set()
    r.panel_files_stamp = lambda: r.stamp
    r.panel_stamps = lambda ids: {s: 0 for s in ids}
    for s in WATCHED:
        r.links[s] = [{'id': f't_{s}', 'status': 'running', 'board': 'default', 'session_id': s}]
        r.cards[f't_{s}'] = {'detail': {'task': {'id': f't_{s}', 'status': 'running', 'session_id': s},
                                        'links': {'parents': [], 'children': []}, 'link_tasks': [], 'events': []},
                             'board': 'default'}
    return f, r, source, calls


def write(r, source, card=None, session=None):
    source['version'] += 1
    r.stamp += 1
    if card:
        r.events.append((card, session))


def test_source_writes_touching_one_chat_reconcile_that_chat_only():
    f, r, source, calls = setup()
    f.watch(WATCHED)
    f.tick()
    assert sorted(calls[0]) == sorted(WATCHED), 'first sight of a chat reconciles it'
    calls.clear()
    for _ in range(5):
        write(r, source, 't_C7', 'C7')
        f.tick()
    assert calls == [['C7']] * 5
    # writes that touch no watched chat's inputs reconcile nobody
    for _ in range(5):
        write(r, source)
        f.tick()
    assert calls == [['C7']] * 5


def test_a_lineage_change_admits_that_chat_without_any_card_event():
    f, r, source, calls = setup()
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    r.lineages['C3'] = ['C3', 'C3-parent']  # compression / membership change
    write(r, source)
    f.tick()
    assert calls == [['C3']]


def test_failed_chat_is_retried_alone_without_another_write():
    f, r, source, calls = setup()
    left = {'C1'}
    r.reconcile_sources = lambda sessions: calls.append(list(sessions)) or (left & set(sessions))
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    left.clear()
    f._reconcile_retry_at = 0
    f.tick()
    assert calls == [['C1']]


def test_unscoped_readers_keep_the_conservative_contract():
    f, r, source, calls = setup()
    r.reconcile_scoped = False
    f.watch(['C1', 'C2'])
    f.tick()
    calls.clear()
    write(r, source)
    f.tick()
    assert calls == [['C1', 'C2']]


def test_tick_timing_and_publish_time_are_recorded_and_in_status():
    f, r, source, calls = setup()
    f.watch(['C1'])
    before = time.time()
    f.tick()
    st = f.status()['timing']
    assert st['ticks'] == 1 and st['tick_started_at'] >= before - 1
    assert st['tick_duration_s'] is not None and st['producer_chats'] == 1
    assert st['last_publish_at'] is not None and st['last_publish_rev'] == f.rev


def test_forget_drops_retry_budgets():
    f, r, source, calls = setup()
    f.watch(['C1'])
    f._budgets = {'reconcile': {'C1': 2}, 'hydrate': {'C1': 1, 'C2': 1}}
    with f.lock:
        f._forget('C1')
    assert f._budgets == {'reconcile': {}, 'hydrate': {'C2': 1}}


def test_timing_file_is_written_atomically(tmp_path):
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
    api = sys.modules.get('_spf_api_timing_test')
    if api is None:
        try:
            spec = importlib.util.spec_from_file_location('_spf_api_timing_test', path)
            api = importlib.util.module_from_spec(spec)
            sys.modules['_spf_api_timing_test'] = api
            spec.loader.exec_module(api)
        except ImportError:
            import pytest
            pytest.skip('plugin_api needs core on the path')
    f, r, source, calls = setup()
    f.watch(['C1'])
    f.tick()
    target = tmp_path / 'timing.json'
    assert api.write_timing(f, target)
    body = json.loads(target.read_text())
    assert body['ticks'] == 1 and body['rev'] == f.rev and body['last_publish_at'] is not None

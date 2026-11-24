"""Done rows show how long the card's runs took, how many restarts, and what its tokens cost."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('_spf_done_stats_feed', ROOT / 'dashboard' / 'feed.py')
feedmod = importlib.util.module_from_spec(_spec)
sys.modules['_spf_done_stats_feed'] = feedmod
_spec.loader.exec_module(feedmod)
_mspec = importlib.util.spec_from_file_location('_spf_done_stats_model', ROOT / 'model.py')
model = importlib.util.module_from_spec(_mspec)
_mspec.loader.exec_module(model)
NOW = 1795281000


def board(runs, events):
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, status TEXT, started_at INTEGER, ended_at INTEGER)')
    c.execute('CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, kind TEXT, payload TEXT)')
    c.executemany('INSERT INTO task_runs (id, task_id, status, started_at, ended_at) VALUES (?,?,?,?,?)', runs)
    c.executemany('INSERT INTO task_events (task_id, run_id, kind, payload) VALUES (?,?,?,?)',
                  [(t, r, k, json.dumps(p)) for t, r, k, p in events])
    return c


def test_reported_cost_runs_and_duration():
    c = board([(1, 't_a', 'crashed', 100, 160), (2, 't_a', 'done', 200, 800), (3, 't_b', 'done', 0, 30)],
              [('t_a', 1, 'worker_result', {'usage': {'output_tokens': 10, 'cost_usd': 0.5}}),
               ('t_a', 2, 'worker_result', {'usage': {'output_tokens': 5, 'cost_usd': 0.1}}),
               ('t_a', 2, 'worker_result', {'usage': {'output_tokens': 20, 'cost_usd': 1.25}})])  # newest receipt wins
    stats = feedmod.done_run_stats(c, ['t_a', 't_b', 't_missing'])
    assert stats['t_a'] == {'runs': 2, 'run_seconds': 660, 'cost_usd': 1.75, 'priced_runs': 2, 'usage_runs': 2, 'tokens': 30}
    assert stats['t_b'] == {'runs': 1, 'run_seconds': 30, 'cost_usd': 0.0, 'priced_runs': 0, 'usage_runs': 0, 'tokens': 0}
    assert 't_missing' not in stats


def test_subscription_tokens_priced_at_list_rate(monkeypatch):
    seen = []

    def fake_cost(usage, route):
        seen.append((usage, route))
        return None if route.get('model', '').startswith('class:') else 2.0
    monkeypatch.setattr(feedmod, '_run_cost', fake_cost)
    c = board([(1, 't_a', 'done', 0, 60), (2, 't_a', 'done', 60, 120)],
              [('t_a', 1, 'run_route', {'model': 'gpt-6.1-sol', 'provider': 'openai-codex'}),
               ('t_a', 1, 'worker_result', {'usage': {'input_tokens': 1000}}),
               ('t_a', 2, 'run_route', {'model': 'class:deep', 'provider': None}),
               ('t_a', 2, 'worker_result', {'usage': {'input_tokens': 500}})])
    s = feedmod.done_run_stats(c, ['t_a'])['t_a']
    assert (s['cost_usd'], s['priced_runs'], s['usage_runs'], s['tokens']) == (2.0, 1, 2, 1500)
    assert seen[0][1]['provider'] == 'openai-codex'


def test_run_cost_maps_subscription_provider(monkeypatch):
    pricing = pytest.importorskip('agent.usage_pricing')
    calls = []

    def estimate(model_name, usage, provider=None, **_):
        calls.append((model_name, provider, usage.input_tokens))
        return pricing.CostResult(amount_usd=pricing.Decimal('0.42'), status='estimated', source='official_docs_snapshot', label='$0.42')
    monkeypatch.setattr(pricing, 'estimate_usage_cost', estimate)
    assert feedmod._run_cost({'input_tokens': 7}, {'model': 'gpt-6.1-sol', 'provider': 'openai-codex'}) == pytest.approx(0.42)
    assert feedmod._run_cost({'input_tokens': 7}, {'model': 'grok-4.6', 'provider': 'xai-oauth'}) == pytest.approx(0.42)
    assert [c[1] for c in calls] == ['openai', 'xai']
    assert feedmod._run_cost({'input_tokens': 7}, {'model': 'class:deep'}) is None
    assert feedmod._run_cost({'cost_usd': 0.3}, {}) == 0.3  # reported cost wins, no estimate
    assert len(calls) == 2


def test_unreadable_board_yields_nothing():
    c = sqlite3.connect(':memory:')
    assert feedmod.done_run_stats(c, ['t_a']) == {}
    c.close()
    assert feedmod.done_run_stats(c, ['t_a']) == {}


def test_with_done_run_stats_only_touches_done_rows():
    c = board([(1, 't_a', 'done', 0, 90), (2, 't_r', 'running', 0, None)], [])
    rows = [{'id': 't_a', 'status': 'done'}, {'id': 't_r', 'status': 'running'}]
    feedmod.with_done_run_stats(rows, c)
    assert rows[0]['run_stats']['runs'] == 1 and 'run_stats' not in rows[1]


def done_row(tid, stats, **kw):
    return {'id': tid, 'gateway': 'hub', 'profile': 'default', 'board': 'default', 'title': 'Shipped',
            'status': 'done', 'completed_at': NOW - 60, 'run_stats': stats, **kw}


def reduce(*rows):
    return model.reduce_panel({'computed_at': NOW, 'tasks': [], 'done': list(rows), 'cards': {}}, now=NOW)


def test_done_row_detail_shows_duration_restarts_and_dollars():
    m = reduce(done_row('t_a', {'runs': 3, 'run_seconds': 2520, 'cost_usd': 3.1, 'priced_runs': 3, 'usage_runs': 3,
                                'tokens': 9_400_000}, result='merged PR'))
    row = m['sections']['done'][0]
    assert row['detail'] == '42m · 2 restarts · $3.10'
    assert row['details'][0].startswith('3 runs · run time 42m · 9.4M tokens · $3.10 for 3 of 3 runs')
    assert row['details'][1] == 'merged PR'


def test_done_row_partial_and_unpriced_and_single_run():
    m = reduce(done_row('t_a', {'runs': 1, 'run_seconds': 300, 'cost_usd': 1.0, 'priced_runs': 1, 'usage_runs': 1, 'tokens': 10}),
               done_row('t_b', {'runs': 2, 'run_seconds': 60, 'cost_usd': 1.0, 'priced_runs': 1, 'usage_runs': 2, 'tokens': 10}),
               done_row('t_c', {'runs': 5, 'run_seconds': 4828, 'cost_usd': 0.0, 'priced_runs': 0, 'usage_runs': 1,
                                'tokens': 18_837_815}))
    by_id = {r['id']: r for r in m['sections']['done']}
    assert by_id['t_a']['detail'] == '5m · $1.00'  # no restarts: none shown
    assert by_id['t_b']['detail'] == '1m · 1 restart · ~$1.00'  # one run could not be priced
    assert by_id['t_c']['detail'] == '1h 20m · 4 restarts · 18.8M tokens'  # never a made-up dollar figure
    assert by_id['t_c']['details'][0].endswith('cost unknown')


def test_done_row_without_stats_keeps_result_detail():
    m = reduce(done_row('t_a', None, result='landed'), done_row('t_b', {'runs': 'x'}, result='kept'))
    assert [r['detail'] for r in m['sections']['done']] == ['landed', 'kept']


def test_done_stats_revision_is_deterministic():
    stats = {'runs': 2, 'run_seconds': 60, 'cost_usd': 1.0, 'priced_runs': 2, 'usage_runs': 2, 'tokens': 10}
    a, b = reduce(done_row('t_a', stats)), reduce(done_row('t_a', dict(stats)))
    c = reduce(done_row('t_a', {**stats, 'cost_usd': 2.0}))
    assert a['revision'] == b['revision'] != c['revision']

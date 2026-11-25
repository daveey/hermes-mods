"""Real dispatcher -> production producer -> view.py -> jsdom sidebar.

Only external provider readings and process spawning are substituted. No dashboard
rows, governor snapshots or board results are synthesized.
"""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

core_fixtures = pytest.importorskip('tests.hermes_cli.test_kanban_governors')
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
from hermes_cli import kanban_lanes as lanes, kanban_governors as gov
from hermes_cli import kanban_llm_pacing_dispatch as pd

board = core_fixtures.board
clean = core_fixtures.clean
ROOT = Path(__file__).resolve().parents[3]
NODE = ROOT / 'desktop-plugins/lanes/tests/render-governors.mjs'


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location('governor_acceptance_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    yield obj
    obj._observations().join(2) if obj._observations.cache_info().currsize else None


def config(home, **extra):
    core_fixtures.model_tier._write_cfg(home, admission={'lanes': {
        'babybear': {'share': 20}, 'other': {'share': 10}}}, **extra)


def render(tmp_path, body, case, **extra):
    if not (NODE.parent / 'node_modules/react').exists():
        pytest.skip('install desktop-plugins/lanes/tests/package.json for jsdom acceptance')
    path = tmp_path / 'view.json'
    path.write_text(json.dumps(dict(view=body, case=case, **extra)))
    result = subprocess.run(['node', str(NODE), str(path)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def view(api):
    # Real production evidence selection (including all-board enumeration), no REST cache.
    return api._lane_view(6, '6h', True)


def test_pool_and_recovery_sidebar(board, api, monkeypatch, tmp_path):
    config(board)
    cfg = json.loads(board.joinpath('config.yaml').read_text())
    for routes in cfg['model_routing']['routes'].values():
        routes.extend([{**routes[0], 'model': 'gpt-6-astra', 'provider': 'openai-codex'},
                       {**routes[0], 'model': 'grok-4.6', 'provider': 'xai-oauth'}])
    board.joinpath('config.yaml').write_text(json.dumps(cfg))
    from hermes_cli import kanban_provider_walls as pw
    monkeypatch.setattr(pw, 'live_pool_serving', lambda *a: True)
    monkeypatch.setattr(pw, 'live_quota_wall', lambda *a: None)
    used = [core_fixtures.RESERVE + 7]
    def pool(provider, now):
        value = used[0] if provider == 'openai-codex' else core_fixtures.RESERVE + 7
        return [core_fixtures.lp.Reading(now, provider + '-account', core_fixtures.lp.SEVEN_DAY, value, now + 86400)]
    monkeypatch.setattr(pd, 'read_pool', pool)
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title='subscription work', assignee='default', tenant='babybear')
        core_fixtures._tick(conn)
        assert gov.snapshot(conn)['cards'][tid]['governor'] == 'llm_pool'
        first = view(api)
        assert first['governors']['idle_total'] == first['governors']['charged_total'] == 1
        render(tmp_path, first, 'pool')
        used[0] = core_fixtures.RESERVE - 45
        monkeypatch.setattr(pd, '_CACHE', {})
        state = json.loads(pd.state_path().read_text())
        for value in state.values():
            if isinstance(value, dict) and 'read_at' in value:
                value['read_at'] -= 601
        pd.state_path().write_text(json.dumps(state))
        core_fixtures._tick(conn)
        recovered = view(api)
        render(tmp_path, recovered, 'recovered')


def test_missing_and_stale_core_and_adapter(board, api, monkeypatch, tmp_path):
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        kb.create_task(conn, title='unknown telemetry', assignee='default', tenant='babybear')
        render(tmp_path, view(api), 'unknown')
        core_fixtures._tick(conn)
        path = gov.snapshot_path(conn)
        doc = json.loads(path.read_text())
        doc['tick_at'] -= 181
        path.write_text(json.dumps(doc))
        render(tmp_path, view(api), 'unknown')


def test_lane_gate_real_counts(board, api, monkeypatch, tmp_path):
    monkeypatch.setattr(lanes, '_human_shell', lambda: True)
    core_fixtures.model_tier._write_cfg(board, admission={'agent_priority_cap': 100, 'lanes': {
        'babybear': {'share': 20, 'gated': True, 'admit_per_day': 2}, 'other': {'share': 10}}})
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        parent = kb.create_task(conn, title='root', assignee='default', tenant='babybear', created_by='user')
        held = kb.create_task(conn, title='gated follow-up', assignee='default', tenant='babybear',
                              creator_task_id=parent, created_by='default')
        core_fixtures._tick(conn)
        assert gov.snapshot(conn)['cards'][held]['governor'] == 'lane_gate'
        render(tmp_path, view(api), 'gate')


def test_cross_board_host_slots_and_release(board, api, monkeypatch, tmp_path):
    config(board)
    core_fixtures._usage(monkeypatch, [10])
    kb.create_board('beta')
    with kbc.connect(board='default') as a, kbc.connect(board='beta') as b:
        first = kb.create_task(a, title='board A work', assignee='default', tenant='babybear')
        kb.create_task(b, title='board B work', assignee='default', tenant='babybear')
        core_fixtures._tick(a, max_in_progress=1, board='default')
        core_fixtures._tick(b, max_in_progress=1, board='beta')
        body = view(api)
        assert body['totals']['open'] == 2
        assert body['governors']['charged_total'] == body['governors']['idle_total'] == 1
        assert body['lanes']['babybear']['boards'] == ['beta', 'default']
        render(tmp_path, body, 'host')
        assert kb.complete_task(a, first, summary='done', expected_run_id=kb.get_task(a, first).current_run_id)
        core_fixtures._tick(b, max_in_progress=1, board='beta')
        render(tmp_path, view(api), 'host-released')


def test_observer_contexts_cover_all_boards_once(board, api, monkeypatch):
    config(board)
    kb.create_board('beta')
    calls = []
    observer = api._observations()
    original = observer.reader
    monkeypatch.setattr(api, '_pr_contexts', lambda conn, *_args, **_kw: {
        conn.execute('PRAGMA database_list').fetchone()[2]: 'source-revision'})
    def capture(contexts):
        calls.append(dict(contexts))
        return original(contexts)
    monkeypatch.setattr(observer, 'reader', capture)
    view(api)
    assert len(calls) == 1
    assert len(calls[0]) == 2  # one combined observation scope, never alternate eviction


def test_secondary_board_read_only_and_cursor(board, api):
    config(board)
    kb.create_board('beta')
    meta = next(b for b in kb.list_boards() if b['slug'] == 'beta')
    import sqlite3
    with api._sibling('board_merge').read_board(meta['db_path']) as db:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('CREATE TABLE forbidden_write (id INTEGER)')
    before, watched = api._capture_cursor()
    assert meta['db_path'] in watched
    assert meta['db_path'] + '.governors.json' in watched
    with kbc.connect(board='beta') as db:
        kb.create_task(db, title='secondary board changed', assignee='default', tenant='babybear')
    after, _ = api._capture_cursor()
    assert before != after


def test_adapter_deadline_uses_its_declared_cadence(board, api, monkeypatch):
    config(board)
    path = board / 'state/governors/mac-mini.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'read_at': 100, 'cadence_s': 10, 'available': True}))
    api._capture_cursor()
    assert 130 in api._governor_deadlines


def test_stale_device_adapter_does_not_become_a_fleet_governor(board, api, monkeypatch, tmp_path):
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        kb.create_task(conn, title='hub card with residual device telemetry', assignee='default', tenant='babybear')
        core_fixtures._tick(conn)
        path = board / 'state/governors/mac-mini.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'version': 1, 'read_at': api.time.time() - 31,
                                    'cadence_s': 10, 'available': True, 'status_counts': {'ready': 9}}))
        body = view(api)
        assert body['governors']['device_footnote']['state'] == 'unknown'
        assert body['governors']['available']
        render(tmp_path, body, 'adapter-stale')


def test_freshness_expires_without_a_file_write(api, monkeypatch):
    monkeypatch.setattr(api._change_source, 'read', lambda _capture: 'unchanged')
    monkeypatch.setattr(api, '_governor_deadlines', (100,))
    monkeypatch.setattr(api.time, 'time', lambda: 99)
    before = api.change_cursor()
    monkeypatch.setattr(api.time, 'time', lambda: 101)
    assert api.change_cursor() != before


pool_hub = core_fixtures.pool_hub


def test_overlap_exclusively_charged(pool_hub, api, monkeypatch, tmp_path):
    config(pool_hub.home, sync={'enabled': True})
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 10])
    monkeypatch.setattr(gov, 'OVERLAP_BUDGET_SECONDS', 60)
    conn = pool_hub.conn
    tid = kb.create_task(conn, title='cannot lease and pool reserved', assignee='default', tenant='babybear',
                         runs_on=list(core_fixtures.pool_wait.LABELS))
    pool_hub.tick()
    receipt = gov.snapshot(conn)['cards'][tid]
    assert receipt['governor'] == 'placement'
    assert [r['governor'] for r in receipt['overlaps']] == ['llm_pool']
    body = view(api)
    assert body['governors']['charged_total'] == body['governors']['idle_total'] == 1
    assert sum(r['cards_charged'] for r in body['governors']['rows']) == 1
    render(tmp_path, body, 'overlap')

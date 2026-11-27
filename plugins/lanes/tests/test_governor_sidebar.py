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


def test_admit_per_day_write_and_next_snapshot(board, api, monkeypatch):
    monkeypatch.setattr(lanes, '_human_shell', lambda: True)
    config(board)
    api.change('babybear', {'admit_per_day': 17})
    from utils import load_yaml_file_readonly
    written = load_yaml_file_readonly(board / 'config.yaml')
    assert written['kanban']['admission']['lanes']['babybear']['admit_per_day'] == 17
    body = view(api)
    assert body['lanes']['babybear']['admit_per_day'] == 17


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
        body = view(api)
        assert body['totals']['held'] == 1
        assert body['governors']['idle_total'] == 2
        assert {card['id'] for card in body['lanes']['babybear']['held_cards']} == {parent, held}
        render(tmp_path, body, 'gate')


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


def test_cross_board_attention_refs_use_source_board(board, api):
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps

    config(board)
    kb.create_board('bb')
    hr = {'action': 'Reply A or B. Unblock = use the choice; archive = drop it.',
          'why_human': 'Only you can choose', 'interaction': 'reply',
          'scope': 'Fixture choice', 'evidence': 'Two supported alternatives'}
    with kbc.connect(board='bb') as conn:
        # Chat membership is sync-owned; ordinary fixture boards omit that table.
        apply_sync_schema_steps(conn)
        tid = kb.create_task(conn, title='Choose route', assignee='default', tenant='babybear')
        assert kb.block_task(conn, tid, kind='needs_input', reason='Choose route', human_request=hr,
                             expected_run_id=kb.get_task(conn, tid).current_run_id)
    body = view(api)
    assert body['attention']['available'], body['attention']
    row, = body['attention']['rows']
    assert row['id'] == tid
    assert row['target']['board'] == 'bb'
    assert row['key'].split(':')[-2:] == ['bb', tid]


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


@pytest.mark.parametrize('facet', ['deadline', 'diagnostics'])
def test_core_cursor_uses_receipt_bound_and_ignores_tick_diagnostics(board, api, monkeypatch, facet):
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        kb.create_task(conn, title='receipt freshness', assignee='default', tenant='babybear')
        core_fixtures._tick(conn)
        path = gov.snapshot_path(conn)
        doc = json.loads(path.read_text())
        doc['freshness_bound_s'] = 105
        path.write_text(json.dumps(doc))
        before, _ = api._capture_cursor()
        if facet == 'deadline':
            assert doc['tick_at'] + 105 in api._governor_deadlines
            return
        doc['tick_completions'] = [*doc.get('tick_completions', []), doc['tick_at'] + 1]
        doc['tick_gap_p95_s'] = 23.5
        doc['tick_at'] += 1
        path.write_text(json.dumps(doc))
        after, _ = api._capture_cursor()
        assert after == before, 'diagnostics and fresh tick do not invalidate decisions'


def test_composed_sdk_mount_order_preserves_real_lane_limits(board, api, monkeypatch, tmp_path):
    core_fixtures.model_tier._write_cfg(board, admission={'lanes': {
        'babybear': {'share': 20, 'max_workers': 3, 'cards_per_day': 7, 'usd_per_day': 12.5},
        'other': {'share': 10}}})
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        kb.create_task(conn, title='composed SDK limits', assignee='default', tenant='babybear')
        core_fixtures._tick(conn)
        expanded = view(api)
        collapsed = api._lane_view(6, '6h', False)
    assert expanded['lanes']['babybear']['limits'] == {
        'max_workers': 3, 'cards_per_day': 7, 'usd_per_day': 12.5}
    # Compact rows need the actual ceiling even while details are collapsed.
    assert collapsed['lanes']['babybear']['limits'] == expanded['lanes']['babybear']['limits']
    if not (NODE.parent / 'node_modules/react').exists():
        pytest.skip('install desktop-plugins/lanes/tests/package.json for jsdom acceptance')
    path = tmp_path / 'composed.json'
    path.write_text(json.dumps({'collapsed': collapsed, 'expanded': expanded}))
    result = subprocess.run(['node', str(NODE.with_name('render-composed-sidebar.mjs')), str(path)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cursor_tolerates_core_without_optional_freshness_helper(board, api, monkeypatch):
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        kb.create_task(conn, title='older core receipt', assignee='default', tenant='babybear')
        core_fixtures._tick(conn)
        path = str(gov.snapshot_path(conn))
        monkeypatch.delattr(gov, 'freshness_bound')
        cursor, watched = api._capture_cursor()
        assert path in watched
        assert (path, None) in cursor, 'unsupported freshness is unavailable, never a fabricated deadline'


def test_registry_version_skew_preserves_online_view_and_accounting(board, api, monkeypatch):
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        kb.create_task(conn, title='new governor on older producer registry', assignee='default', tenant='babybear')
        core_fixtures._tick(conn)
        registry = gov.registry()
        monkeypatch.setattr(gov, 'registry', lambda: [r for r in registry if r['id'] != 'llm_pool'])
        body = view(api)
        assert body['configured']
        projected = body['governors']
        unknown = next(r for r in projected['rows'] if r['id'] == 'llm_pool')
        assert unknown['state'] == 'unknown'
        assert unknown['cards_charged'] == 1
        assert sum(r['cards_charged'] for r in projected['rows']) == projected['known_idle_total'] == 1


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


@pytest.mark.parametrize('failure', ['flow', 'truncated'])
def test_incomplete_lane_evidence_is_unknown(board, api, monkeypatch, tmp_path, failure):
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    with kbc.connect() as conn:
        for n in range(2):
            kb.create_task(conn, title=f'held card {n}', assignee='default', tenant='babybear')
        if failure == 'truncated':
            monkeypatch.setattr(gov, 'MAX_CARDS', 1)
        core_fixtures._tick(conn)
        if failure == 'flow':
            def unavailable(*args, **kwargs):
                raise RuntimeError('flow source unavailable')
            monkeypatch.setattr(api.flow_core()[1], 'snapshot', unavailable)
        body = view(api)
        row = body['lanes']['babybear']
        assert body['governors']['known_idle_total'] == 2
        assert row['governor_held'] is None
        assert 'top_reason' not in row
        assert row['holds'] == [] and row['waits'] == dict.fromkeys(('parents', 'scheduled', 'deploy_ci', 'triage'))
        assert not row['held_cards']
        assert not row['steps']
        render(tmp_path, body, 'incomplete')


pool_hub = core_fixtures.pool_hub


def test_failed_board_keeps_healthy_sibling_lane_known(board, api, monkeypatch):
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    kb.create_board('beta')
    with kbc.connect() as a, kbc.connect(board='beta') as b:
        kb.create_task(a, title='broken flow', assignee='default', tenant='babybear')
        kb.create_task(b, title='healthy flow', assignee='default', tenant='other')
        core_fixtures._tick(a)
        core_fixtures._tick(b, board='beta')
        original = api.flow_core()[1].snapshot
        def read(conn, *args, **kwargs):
            if gov._board(conn) == 'default':
                raise RuntimeError('default flow unavailable')
            return original(conn, *args, **kwargs)
        monkeypatch.setattr(api.flow_core()[1], 'snapshot', read)
        body = view(api)
        assert body['lanes']['babybear']['governor_held'] is None
        assert body['lanes']['other']['governor_held'] == 1
        assert len(body['lanes']['other']['held_cards']) == 1


@pytest.mark.parametrize('failed_board', ['default', 'beta'])
def test_partial_flow_status_is_unknown_not_zero(board, api, monkeypatch, tmp_path, failed_board):
    config(board)
    core_fixtures._usage(monkeypatch, [10])
    kb.create_board('beta')
    with kbc.connect() as a, kbc.connect(board='beta') as b:
        kb.create_task(a, title='first board running', assignee='default', tenant='babybear')
        kb.create_task(b, title='second board running', assignee='default', tenant='other')
        core_fixtures._tick(a)
        core_fixtures._tick(b, board='beta')
        original = api.flow_core()[1].snapshot
        def read(conn, *args, **kwargs):
            if gov._board(conn) == failed_board:
                raise RuntimeError('fixture flow failure')
            return original(conn, *args, **kwargs)
        monkeypatch.setattr(api.flow_core()[1], 'snapshot', read)
        body = view(api)
        failed_lane = 'babybear' if failed_board == 'default' else 'other'
        healthy_lane = 'other' if failed_board == 'default' else 'babybear'
        assert body['summary_complete'] is True
        assert body['totals']['running'] == 2
        assert body.get('status', {}).get('run') is None
        assert body['lanes'][failed_lane].get('status', {}).get('run') is None
        assert body['lanes'][healthy_lane]['status']['run'] == 1
        assert body['sidebar']['cost_today'] is None
        assert 'fixture flow failure' in body['flow_error']
        assert not body.get('attention', {}).get('available', False)
        render(tmp_path, body, 'partial-status', failed_lane=failed_lane, healthy_lane=healthy_lane)


@pytest.mark.parametrize('failed_board', ['default', 'beta'])
def test_slow_board_does_not_expire_healthy_sibling(board, api, monkeypatch, tmp_path, failed_board):
    from types import SimpleNamespace
    # Advance only the board reader's CPU clock: no busy loop or global time patch.
    merge = api._sibling('board_merge')
    clock = [0.0]
    monkeypatch.setattr(merge, 'thread_time', lambda: clock[0])
    config(board)
    core_fixtures._usage(monkeypatch, [10])
    kb.create_board('beta')
    with kbc.connect() as a, kbc.connect(board='beta') as b:
        kb.create_task(a, title='first board running', assignee='default', tenant='babybear')
        kb.create_task(b, title='second board running', assignee='default', tenant='other')
        core_fixtures._tick(a)
        core_fixtures._tick(b, board='beta')
        original = api.flow_core()[1].snapshot
        def slow(conn, *args, **kwargs):
            if gov._board(conn) == failed_board:
                clock[0] += 3600  # Well beyond any board budget, without sleeping.
                # Exercise SQLite's real expired progress handler, not a synthetic exception.
                conn.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n').fetchone()
            return original(conn, *args, **kwargs)
        monkeypatch.setattr(api.flow_core()[1], 'snapshot', slow)
        body = view(api)
        failed_lane = 'babybear' if failed_board == 'default' else 'other'
        healthy_lane = 'other' if failed_board == 'default' else 'babybear'
        assert body['configured'] is True
        assert body['summary_complete'] is True
        assert body['lanes'][healthy_lane]['status']['run'] == 1
        assert body['lanes'][failed_lane].get('status', {}).get('run') is None
        assert failed_board in body['flow_error']
        assert 'interrupted' in body['flow_error']
        render(tmp_path, body, 'partial-status', failed_lane=failed_lane, healthy_lane=healthy_lane)
        monkeypatch.setattr(api.flow_core()[1], 'snapshot', original)
        assert view(api)['lanes'][failed_lane]['status']['run'] == 1


def test_missing_board_summary_is_unknown_not_zero(board, api, monkeypatch, tmp_path):
    import sqlite3
    from contextlib import contextmanager
    config(board)
    core_fixtures._usage(monkeypatch, [10])
    kb.create_board('beta')
    with kbc.connect() as a, kbc.connect(board='beta') as b:
        kb.create_task(a, title='healthy running', assignee='default', tenant='babybear')
        kb.create_task(b, title='unreadable waiting', assignee='default', tenant='other')
        core_fixtures._tick(a)
        core_fixtures._tick(b, max_spawn=0, board='beta')
        healthy = view(api)
        assert healthy['totals']['not_ready'] == 1
        merge = api._sibling('board_merge')
        original = merge.read_board
        @contextmanager
        def read(path, **kwargs):
            if '/beta/' in str(path):
                raise sqlite3.OperationalError('fixture unavailable board')
            with original(path, **kwargs) as db:
                yield db
        monkeypatch.setattr(merge, 'read_board', read)
        partial = view(api)
        render(tmp_path, partial, 'partial-summary')
        assert partial['summary_complete'] is False
        assert 'beta: fixture unavailable board' in partial['flow_error']
        failed_scope = next(s for s in partial['governors']['scope'] if s['board'] == 'beta')
        assert failed_scope['reason'] == 'fixture unavailable board'
        assert partial['totals']['not_ready'] is None
        assert all(row['not_ready'] is None and row['actual'] is None for row in partial['lanes'].values())
        monkeypatch.setattr(merge, 'read_board', original)
        recovered = view(api)
        assert recovered['summary_complete'] is True
        assert recovered['totals']['not_ready'] == 1


def test_failed_cursor_keeps_healthy_changes_and_recovery(board, api, monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    config(board)
    kb.create_board('beta')
    kb.create_board('gamma')
    merge = api._sibling('board_merge')
    original = merge.read_board
    failed = [True]
    @contextmanager
    def read(path, **kwargs):
        if failed[0] and '/beta/' in str(path):
            raise sqlite3.OperationalError('fixture sibling failure')
        with original(path, **kwargs) as db:
            yield db
    monkeypatch.setattr(merge, 'read_board', read)
    # Public change_cursor owns semantic capture; no source-notification mocking.
    before = api.change_cursor()
    meta = next(b for b in kb.list_boards() if b['slug'] == 'beta')
    assert str(meta['db_path']) in api._capture_cursor()[1]
    with kbc.connect(board='gamma') as db:
        kb.create_task(db, title='healthy sibling changed', assignee='default', tenant='other')
    after = api.change_cursor()
    assert after != before
    failed[0] = False
    with kbc.connect(board='beta') as db:
        kb.create_task(db, title='recovered sibling changed', assignee='default', tenant='other')
    assert api.change_cursor() != after


def test_governor_receipts_survive_sibling_registry_failure(board, api, monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    kb.create_board('beta')
    with kbc.connect(board='beta') as conn:
        kb.create_task(conn, title='independent sidecar evidence', assignee='default', tenant='other')
        core_fixtures._tick(conn, board='beta')
        expected = gov.snapshot(conn)
    merge = api._sibling('board_merge')
    original = merge.read_board
    @contextmanager
    def fail(path, **kwargs):
        if '/beta/' in str(path):
            raise sqlite3.OperationalError('SQLite tracking registry is busy')
        with original(path, **kwargs) as db:
            yield db
    monkeypatch.setattr(merge, 'read_board', fail)
    # Registry contention arises inside the opener, not only the flow wrapper.
    from hermes_cli import kanban_lockguard as guard
    original_connect = guard.connect
    def busy_connect(path, *args, **kwargs):
        if '/beta/' in str(path):
            raise sqlite3.OperationalError('SQLite tracking registry is busy')
        return original_connect(path, *args, **kwargs)
    monkeypatch.setattr(guard, 'connect', busy_connect)
    projected = view(api)['governors']
    row = next(r for r in projected['rows'] if r['id'] == 'llm_pool')
    assert row['cards_charged'] == expected['charged_total'] == 1
    assert row['state'] == 'on'
    source = next(s for s in projected['scope'] if s['board'] == 'beta')
    assert source['available'] is True and source['flow_available'] is False
    assert 'tracking registry is busy' in source['reason']


def test_sidecar_reader_survives_real_tracking_lock(board, api, monkeypatch):
    import threading
    from hermes_cli import sqlite_safe_read, kanban_lockguard as guard
    import pytest
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    kb.create_board('beta')
    with kbc.connect(board='beta') as conn:
        kb.create_task(conn, title='receipt survives registry contention', assignee='default', tenant='other')
        core_fixtures._tick(conn, board='beta')
        expected = gov.snapshot(conn)
        path = next(r[2] for r in conn.execute('PRAGMA database_list') if r[1] == 'main')
    entered, release = threading.Event(), threading.Event()
    def hold():
        with sqlite_safe_read._live_lock:
            entered.set()
            release.wait(10)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert entered.wait(5)
        import sqlite3
        with pytest.raises(sqlite3.OperationalError, match='tracking registry is busy'):
            guard.connect(path, timeout=.25)
        result = api._sibling('governors').read_snapshot(path=path, board='beta')
        assert result['available'] is True
        assert result['charged_total'] == expected['charged_total'] == 1
        assert result['governors'] == expected['governors']
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()


def test_governor_receipts_survive_flow_failure_and_match_cli(board, api, monkeypatch, capsys):
    import argparse
    from hermes_cli import kanban_governors_cli as cli
    config(board)
    core_fixtures._usage(monkeypatch, [core_fixtures.RESERVE + 7])
    kb.create_board('beta')
    with kbc.connect() as a, kbc.connect(board='beta') as b:
        kb.create_task(a, title='default known charge', assignee='default', tenant='babybear')
        kb.create_task(b, title='beta known charge', assignee='default', tenant='other')
        core_fixtures._tick(a)
        core_fixtures._tick(b, board='beta')
        cli.governors_command(argparse.Namespace(all_boards=True, json=True, board=None))
        expected = json.loads(capsys.readouterr().out)
        original = api.flow_core()[1].snapshot
        def fail(conn, *args, **kwargs):
            if gov._board(conn) == 'default':
                raise RuntimeError('fixture flow failure after valid receipt')
            return original(conn, *args, **kwargs)
        monkeypatch.setattr(api.flow_core()[1], 'snapshot', fail)
        projected = view(api)['governors']
        pool = next(r for r in projected['rows'] if r['id'] == 'llm_pool')
        assert pool['state'] == 'on'
        assert pool['cards_charged'] == sum(r['cards'] for s in expected['boards'] for r in s['governors'] if r['governor'] == 'llm_pool') == 2
        source = next(s for s in projected['scope'] if s['board'] == 'default')
        assert source['available'] is True
        assert source['flow_available'] is False
        assert 'fixture flow failure' in source['reason']


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

"""SessionState v2 P0: pin what lanes flow -> attention -> view says today for every shared core scenario.

Each scenario of core's ``tests/fixtures/session-state-v2/scenarios.json`` (found through HERMES_CORE_SOURCE) is written
into its own real board (core's ``kanban_db``, a private HERMES_HOME) and run through the real core flow snapshot, this
plugin's real ``attention.project`` and ``view.project``. The expected values in ``session_state_v2_goldens.json`` were
generated from that execution and reviewed; ``SESSION_STATE_V2_UPDATE_GOLDENS=1`` regenerates them. A later
SessionState reducer must reproduce them or change them on purpose.

Lanes counts cards, not cardless runtime. Those cases retain the actual core runtime/session envelope alongside
the unchanged lanes result; the stale replica is a separate source selected by real core source readers.
"""
import contextlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location('lanes_session_state_v2_support', Path(__file__).with_name('session_state_v2_support.py'))
assert _spec is not None and _spec.loader is not None
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
import test_api as fixtures

pytest.importorskip('fastapi')
DATA, WHY = S.load_scenarios()
pytestmark = pytest.mark.skipif(DATA is None, reason=WHY or 'no compatible core')
api = fixtures.api


@pytest.fixture
def board(tmp_path, monkeypatch, api):
    pytest.importorskip('hermes_cli.kanban_db')
    pytest.importorskip('hermes_cli.kanban_lanes_flow')
    sources = pytest.importorskip('hermes_cli.kanban_lanes_sources')
    from hermes_cli import kanban_db_connect as kbc, kanban_lanes as lanes
    import hermes_constants
    for key in ('HERMES_HOME', 'HERMES_KANBAN_HOME'):
        monkeypatch.setenv(key, str(tmp_path))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(hermes_constants, 'get_default_hermes_root', lambda: tmp_path)
    (tmp_path / 'config.yaml').write_text(json.dumps(
        {'kanban': {'admission': {'lanes': {'work': {'share': 1}}, 'lane_default': 'work'}}}))
    monkeypatch.setattr(lanes, '_LAST_GOOD', [])
    path = tmp_path / 'fixture.db'
    conn = kbc.connect(path)

    @contextlib.contextmanager
    def connect():
        with contextlib.closing(kbc.connect(path)) as c:
            yield c
    monkeypatch.setattr(kbc, 'connect_closing', connect)
    api._projector_enabled = False
    yield api, conn, connect, sources, monkeypatch, tmp_path
    conn.close()


def observe(board, scenario):
    api, conn, connect, sources, monkeypatch, home = board
    now = DATA['now']
    ids = S.materialize(conn, DATA, scenario, now)
    remote_id, replica_rows = S.replica_case(DATA, scenario, monkeypatch, home)
    if remote_id:
        ids['card'] = remote_id
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    conn.commit()
    mods = api.core()
    assert mods is not None and api.flow_core() is not None, 'core has no lanes support'
    from hermes_cli.kanban_lanes_flow_pr import Reader
    out = {}
    if replica_rows:
        out['replica'] = [{k: r[k] for k in ('id', 'source', 'remote', 'gateway', 'stale', 'synced_at', 'status')}
                          for r in replica_rows]
    if scenario.get('card') is None:
        canonical = S.canonical_case(DATA, scenario)
        out['runtime_session'] = canonical['session_row']
    with connect() as c, sources.read_view(c) as evidence:
        snap, raw = api._project_evidence(mods, c, evidence, 6.0, '6h', False, sources=True,
                                          reader=Reader(), metrics_now=now)
        assert raw['configured'], raw
        if remote_id:
            assert evidence.conn is not c
            assert evidence.rows[0]['id'] == remote_id
        out['cards'] = sorted(({k: card.get(k) for k in ('step', 'reason', 'status', 'lane', 'wait', 'governor', 'age_h')}
                               for card in raw['cards']), key=lambda c: json.dumps(c, sort_keys=True))
        out['attention'] = raw['attention']
        views = {}
        for name, expanded in (('collapsed', False), ('expanded', True)):
            view = api._view_module().project(snap, raw, expanded)
            views[name] = {'status': view['status'], 'totals': view['totals'], 'gates': view['gates'],
                           'attention_rows': len(view['attention']['rows'])}
        out['view'] = views['collapsed']
        out['expanded_view'] = 'same' if views['expanded'] == views['collapsed'] else views['expanded']
    text = json.dumps(out, sort_keys=True)
    for label, cid in ids.items():
        if cid:
            text = text.replace(cid, f'<{label}>')
    return json.loads(text)


@pytest.mark.parametrize('scenario', S.scenario_params(DATA))
def test_scenario_matches_golden(board, scenario):
    seen = observe(board, scenario)
    print(scenario['id'], json.dumps(seen, sort_keys=True))
    if os.environ.get(S.UPDATE):
        golden = json.loads(S.GOLDEN.read_text()) if S.GOLDEN.exists() else {}
        golden[scenario['id']] = seen
        S.GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + '\n')
        return
    assert seen == json.loads(S.GOLDEN.read_text())[scenario['id']]


def test_support_import_ignores_same_name_module_cache(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'session_state_v2_support', object())
    spec = importlib.util.spec_from_file_location('_lanes_v2_collision_probe', __file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.S.GOLDEN == S.GOLDEN
    assert module.S.materialize.__code__.co_argcount == 4


def test_golden_covers_exactly_the_core_scenarios():
    assert sorted(json.loads(S.GOLDEN.read_text())) == sorted(s['id'] for s in DATA['scenarios'])

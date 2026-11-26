"""Reviewer probes: real core Reader + real subprocess (fake gh binary), no fake store."""
import contextlib
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parents[1] / 'dashboard' / 'plugin_api.py'
URL = 'https://github.com/example/repo/pull/1'


def load():
    spec = importlib.util.spec_from_file_location('lanes_review_api', PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def board(tmp_path, monkeypatch):
    kbc = pytest.importorskip('hermes_cli.kanban_db_connect')
    from hermes_cli import kanban_lanes as lanes, kanban_lanes_cli as cli
    import hermes_constants
    import subprocess
    from hermes_cli import kanban_db_dispatch_pr_state as st
    root = tmp_path / 'home'
    root.mkdir()
    for key in ('HERMES_HOME', 'HERMES_KANBAN_HOME'):
        monkeypatch.setenv(key, str(root))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_BOARD', raising=False)
    monkeypatch.setattr(hermes_constants, 'get_default_hermes_root', lambda: root)
    (root / 'config.yaml').write_text(json.dumps({'kanban': {'admission': {'lanes': {'work': {'share': 1}}, 'lane_default': 'work'}}}))
    monkeypatch.setattr(lanes, '_LAST_GOOD', [])
    monkeypatch.setattr(cli, '_fleet', lambda *a, **k: None)
    path = root / 'fixture.db'
    with contextlib.closing(kbc.connect(path)) as conn:
        conn.execute("INSERT INTO tasks(id,title,status,created_at,created_by,tenant) VALUES('t_pr','pr','ready',?,'user','work')", (int(time.time()) - 60,))
        conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_pr','review_requested',?,?)",
                     (int(time.time()), json.dumps({'metadata': {'pr': URL}})))
        conn.commit()

    @contextlib.contextmanager
    def connect():
        with contextlib.closing(kbc.connect(path)) as conn:
            yield conn
    monkeypatch.setattr(kbc, 'connect_closing', connect)
    gh = tmp_path / 'gh.py'
    gh.write_text(
        "import sys, json, time\n"
        "time.sleep(float(__import__('os').environ.get('FAKE_GH_SLEEP', '2')))\n"
        "head = 'a' * 40\n"
        "pr = {'state': 'OPEN', 'headRefOid': head, 'reviewDecision': 'APPROVED', 'author': {'login': 'a'}}\n"
        "if sys.argv[1:3] == ['api', 'graphql']:\n"
        " pr['reviews'] = {'pageInfo': {'hasPreviousPage': False}, 'nodes': []}\n"
        " pr['commits'] = {'nodes': [{'commit': {'oid': head, 'statusCheckRollup': {'contexts': {"
        "'pageInfo': {'hasNextPage': False}, 'nodes': [{'__typename': 'CheckRun', 'isRequired': True, "
        "'status': 'COMPLETED', 'conclusion': 'SUCCESS'}]}}}}]}\n"
        " print(json.dumps({'data': {'repository': {'pullRequest': pr}}}))\n"
        "elif sys.argv[1:3] == ['pr', 'view']:\n"
        " print(json.dumps(pr))\n"
        "else:\n sys.exit('unexpected fake gh request: ' + repr(sys.argv[1:]))\n")
    monkeypatch.setattr(st, '_gh_argv', lambda: [sys.executable, str(gh)])
    calls = []
    real_run = subprocess.run

    def run(argv, *a, **k):
        if str(gh) in map(str, argv):
            calls.append(threading.current_thread().name)
        return real_run(argv, *a, **k)
    monkeypatch.setattr(subprocess, 'run', run)
    api = load()
    # This legacy fixture monkeypatches source selection and gh in-process.
    # Dedicated worker parity has its own genuinely configured subprocess fixture.
    api._projector_enabled = False
    yield api, connect, calls
    if api._observations.cache_info().currsize:
        api._observations().join(30)


def pr_of(body):
    c = next(c for c in body['cards'] if c['id'] == 't_pr')
    return (c.get('step'), c.get('reason'))


def test_real_reader_off_request_thread_then_publishes(board):
    api, connect, calls = board
    main = threading.current_thread().name
    t = time.monotonic()
    body = api.flow('6h')
    assert time.monotonic() - t < 3, time.monotonic() - t
    unobserved = pr_of(body)
    print('unobserved', unobserved)
    api._observations().join(20)
    assert calls and main not in calls, calls
    api._written()
    body = api.flow('6h')
    print('published pr', pr_of(body), 'gh calls', len(calls))
    assert api._observations().reader(api._observations().contexts).get(URL)['approved'] is True
    assert pr_of(body) != unobserved


@pytest.mark.parametrize('head_kind', ['commented', 'external_watch'])
def test_busy_board_still_publishes(board, head_kind):
    """Unrelated events (heartbeats) every ~1 s, as on the live default board."""
    api, connect, calls = board
    seen = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with connect() as conn:
            conn.execute("INSERT INTO task_events(task_id,kind,created_at) VALUES('t_other','heartbeat',?)", (int(time.time()),))
            conn.execute("INSERT INTO task_events(task_id,kind,created_at) VALUES('t_pr','heartbeat',?)", (int(time.time()),))
            conn.commit()
        api._written()  # what the WS _refresh_cursor does on each cursor change
        seen.append(pr_of(api.flow('6h')))
        time.sleep(1)
    print('gh subprocess calls during churn:', len(calls), 'renders:', seen, 'revision', api._observations().revision)
    assert ('merge', 'review') in seen, 'PR never classified on a busy board'
    assert len(calls) == 2, 'unrelated events must not repeat network observations'
    with connect() as conn:
        conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_pr',?,?,?)",
                     (head_kind, int(time.time()), json.dumps({'metadata': {'published_pr': URL, 'head': 'changed'}})))
        conn.commit()
    api._written()
    assert pr_of(api.flow('6h'))[0] != 'merge', 'new PR/head evidence reused old approval'


@pytest.mark.parametrize('through_shared_store', [False, True])
def test_local_status_transition_renders_without_provider_refill(board, monkeypatch, through_shared_store):
    """Running -> scheduled changes presentation, not the PR4077 provider evidence."""
    from hermes_cli import kanban_lanes_flow as flow
    api, connect, calls = board
    monkeypatch.setenv('FAKE_GH_SLEEP', '0')
    observer = api._observations()
    if not through_shared_store:
        observer.shared = None
    with connect() as conn:
        conn.execute("UPDATE tasks SET status='running' WHERE id='t_pr'")
        conn.commit()
    first = api.lane_view(6, '6h', True)
    assert first['totals']['running'] == 1
    observer.join(10)
    assert len(calls) == 2
    with connect() as conn:
        before = api._pr_contexts(conn, flow, evidence=flow.Evidence(conn))
    for status in ('scheduled', 'ready', 'running', 'scheduled'):
        with connect() as conn:
            conn.execute("UPDATE tasks SET status=? WHERE id='t_pr'", (status,))
            conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_pr',?,?,?)",
                         (status, int(time.time()), json.dumps({'awaiting': {'kind': 'clock', 'why': 'fixture'}})))
            conn.commit()
        api._written()
        rendered = api.lane_view(6, '6h', True)
        observer.join(10)
        # Assert presentation independently of the provider key/call budget.
        assert rendered['totals']['running'] == int(status == 'running')
        card = next(c for c in rendered['flow']['cards'] if c['id'] == 't_pr')
        assert card['status'] == status
        assert len(calls) == 2, 'local status changes refetched unchanged GitHub evidence'
        with connect() as conn:
            assert api._pr_contexts(conn, flow, evidence=flow.Evidence(conn)) == before
    # An actual PR/head event must still discard the cached approval immediately.
    with connect() as conn:
        conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_pr','external_watch',?,?)",
                     (int(time.time()), json.dumps({'metadata': {'head_sha': 'new-head'}})))
        conn.commit()
    api._written()
    api.flow('6h')
    observer.join(10)
    assert len(calls) == 4


@pytest.mark.parametrize('kind', ['commented', 'respawn_guarded', 'dispatch_skipped', 'wake_episode_started'])
def test_same_card_dispatch_churn_reuses_pr_observation(board, monkeypatch, kind):
    api, connect, calls = board
    monkeypatch.setenv('FAKE_GH_SLEEP', '0')
    api.flow('6h')
    api._observations().join(10)
    assert len(calls) == 2  # retain the core's head confirmation
    with connect() as conn:
        conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_pr',?,?,?)",
                     (kind, int(time.time()), json.dumps({'reason': 'active_pr'})))
        conn.commit()
    api._written()
    assert pr_of(api.flow('6h')) == ('merge', 'review')
    api._observations().join(10)
    assert len(calls) == 2, 'operational churn must not evict GitHub evidence'
    with connect() as conn:
        conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_pr','external_watch',?,?)",
                     (int(time.time()), json.dumps({'metadata': {'head_sha': 'new'}})))
        conn.commit()
    api._written()
    assert pr_of(api.flow('6h'))[0] != 'merge'
    api._observations().join(10)
    assert len(calls) == 4

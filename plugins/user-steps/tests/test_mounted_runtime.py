"""Real core mounting/profile scope; no lifespan entered by this client."""
import sys

import pytest

pytest.importorskip('tui_gateway.server')
from fastapi.testclient import TestClient
from hermes_cli import web_server, web_server_dashboard as dashboard
from test_decisions import ROOT, request
from tests.tui_gateway.test_prompt_admission import owner  # noqa: F401


@pytest.fixture
def mounted(monkeypatch):
    app = web_server.app
    routes = list(app.router.routes)
    lifespan = app.router.lifespan_context
    plugin = dict(name='user-steps', source='bundled', _dir=str(ROOT / 'dashboard'),
                  _api_file='plugin_api.py')
    monkeypatch.setattr(web_server, '_get_dashboard_plugins', lambda **kw: [plugin])
    from hermes_cli import plugins_cmd
    monkeypatch.setattr(plugins_cmd, '_get_enabled_set', lambda: {'user-steps'})
    monkeypatch.setattr(plugins_cmd, '_get_disabled_set', lambda: set())
    # Exercise the real hot-mount fallback, not a manually included router lifespan.
    dashboard._register_plugin_api_fallback(app)
    client = TestClient(app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    assert client.get('/api/plugins/user-steps/ping').status_code == 200
    api = sys.modules['hermes_dashboard_plugin_user-steps']
    try:
        yield client, api
    finally:
        client.close()
        app.router.routes[:] = routes
        app.router.lifespan_context = lifespan


def test_hot_mounted_router_recovers_pending_without_lifespan(owner, mounted, monkeypatch):
    db, session, params, started = owner
    client, api = mounted
    core = api._load('core')
    monkeypatch.setattr(core, '_state_db', lambda: db.db_path)
    own = core.server_owner(params['stored_session_id'])
    st = core.ActionStore()
    payload = request(); payload['owner'] = own
    r = st.create(own, payload)
    recorded = st.decide(own, dict(requestId=r['requestId'], expectedRevision=1,
                                 choiceId='approve', idempotencyKey='crash-before-dispatch'))
    oid = recorded['operation']['operationId']
    # New router instance models a restart/hot reload after the durable decision.
    routes = web_server.app.router.routes
    routes[:] = [r for r in routes if not getattr(r, 'path', '').startswith('/api/plugins/user-steps/')]
    assert client.get('/api/plugins/user-steps/requests', params={'session': own['storedSessionId']}).status_code == 200
    assert st.operation(own, oid=oid)['operation']['dispatchState'] == 'delivered'
    assert len(started) == 1
    assert len(db.get_messages(own['storedSessionId'])) == 1
    client.get('/api/plugins/user-steps/requests', params={'session': own['storedSessionId']})
    assert len(started) == 1


def test_secondary_profile_recovers_only_its_outbox_and_routes_owner_events(owner, mounted, tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_cli.web_server_profiles import _config_profile_scope
    from hermes_state import SessionDB
    from tui_gateway import server

    launch_db, session, params, started = owner
    client, api = mounted
    root = tmp_path / 'profiles'
    secondary = root / 'secondary'
    secondary.mkdir(parents=True)
    monkeypatch.setattr(profiles, '_get_profiles_root', lambda: root)
    key = params['stored_session_id']
    # Deliberately collide stored IDs: profile, not session ID alone, is authority.
    db = SessionDB(db_path=secondary / 'state.db')
    db.create_session(key, 'desktop')
    session['profile_home'] = str(secondary)
    core = api._load('core')
    with _config_profile_scope('secondary'):
        own = core.server_owner(key)
        assert own['profile'] == 'secondary'
        st = core.ActionStore()
        payload = request(); payload['owner'] = own
        r = st.create(own, payload)
        recorded = st.decide(own, dict(requestId=r['requestId'], expectedRevision=1,
                                     choiceId='approve', idempotencyKey='secondary-crash'))
    oid = recorded['operation']['operationId']
    base = '/api/plugins/user-steps'
    assert client.get(f'{base}/operations/{oid}', params={'session': key}).status_code == 404
    assert not started
    class Transport:
        def __init__(self):
            self.frames = []
        def write(self, frame):
            self.frames.append(frame)
            return True

    transport, stdio = Transport(), Transport()
    session['transport'] = transport
    monkeypatch.setattr(server, '_stdio_transport', stdio)
    response = client.get(f'{base}/operations/{oid}', params={'session': key, 'profile': 'secondary'})
    assert response.status_code == 200, response.text
    assert response.json()['operation']['dispatchState'] == 'delivered', response.text
    assert len(started) == 1
    assert launch_db.get_messages(key) == []
    rows = db.get_messages_as_conversation(key, include_row_ids=True)
    assert len(rows) == 1 and rows[0]['role'] == 'user'
    assert oid in rows[0]['content']
    assert rows[0]['_row_id'] > 0
    assert session['transport'] is transport

    # Exercise the admitted callback and REAL terminal-event producer/routing.
    # No model call: force the existing build-wait failure path.
    monkeypatch.setattr(server, '_wait_agent_for_prompt', lambda *a: {'error': {'message': 'test build unavailable'}})
    started[0]()
    assert transport.frames
    assert not stdio.frames
    sid = next(sid for sid, s in server._sessions.items() if s is session)
    assert all(f['params']['session_id'] == sid for f in transport.frames)
    terminal = next(f for f in transport.frames if f['params']['type'] == 'message.complete')
    assert terminal['params']['payload']['status'] == 'error'
    assert terminal['params']['payload']['error_surface']['code'] == 'agent_init_failed'
    assert session['transport'] is transport
    # Retry reads the same durable admission, never schedules another turn.
    again = client.post(f'{base}/operations/{oid}/retry', params={'session': key, 'profile': 'secondary'})
    assert again.status_code == 200, again.text
    assert len(started) == 1
    assert len(db.get_messages(key)) == 1
    db.close()

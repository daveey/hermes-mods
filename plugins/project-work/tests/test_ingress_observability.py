"""Every Slack event the middleware sees leaves a log line, and tool refusals say why.

Regression for alpha-askeve (2026-10-30): all traffic was in another channel, so the
middleware ran but logged nothing, and project_work_request answered only
"PermissionError" with no detail.
"""
import asyncio
import contextvars
import importlib
import json
import sys
import types

import pytest
from conftest import PACKAGE

im = importlib.import_module(PACKAGE + '.ingress')
cm = importlib.import_module(PACKAGE + '.client')


def event(**fields):
    return {'type': 'event_callback', 'team_id': 'team', 'event': {
        'type': 'message', 'channel': 'channel', 'user': 'owner', 'ts': 'm1',
        'text': 'hello', **fields}}


def run(ingress, body):
    async def next_(): return 'routed'
    return asyncio.run(ingress.middleware(body, next_, client=None))


def make(env, tmp_path):
    service, ctx, _ = env
    return im.Ingress(service.policy, None, cm.DurableIngressQueue(tmp_path / 'q' / 'q.db', None))


def test_out_of_channel_event_is_logged(env, tmp_path, caplog):
    ingress = make(env, tmp_path)
    caplog.set_level('INFO')
    assert run(ingress, event(channel='elsewhere', ts='e1', bot_id='BEVE')) == 'routed'
    assert ('Project ingress ignored ts=e1 channel=elsewhere: outside project channel channel '
            '(type=message bot=True)') in caplog.text


def test_non_event_body_is_logged_without_failing(env, tmp_path, caplog):
    ingress = make(env, tmp_path)
    caplog.set_level('INFO')
    caplog.set_level('DEBUG')
    assert run(ingress, {'type': 'block_actions'}) == 'routed'
    assert 'Project ingress ignored: not an event callback (type=block_actions)' in caplog.text


def install_turn(monkeypatch, **values):
    names = ('PLATFORM', 'PROFILE', 'SCOPE_ID', 'CHAT_ID', 'MESSAGE_ID', 'USER_ID', 'THREAD_ID')
    variables = {'HERMES_SESSION_' + n: contextvars.ContextVar(n, default=values.get(n, ''))
                 for n in names}
    monkeypatch.setitem(sys.modules, 'gateway', types.SimpleNamespace(
        session_context=types.SimpleNamespace(_VAR_MAP=variables)))


def tool_handlers(ingress):
    handlers = {}
    class Registry:
        def register_tool(self, **kwargs): handlers[kwargs['name']] = kwargs['handler']
    im.register_tools(Registry(), ingress)
    return handlers


def test_tool_refusal_names_the_project_channel(env, tmp_path, monkeypatch):
    service, ctx, _ = env
    ingress = make(env, tmp_path)
    install_turn(monkeypatch, PLATFORM='slack', PROFILE=ctx.profile, SCOPE_ID='team',
                 CHAT_ID='elsewhere', MESSAGE_ID='e1', USER_ID='UEVE')
    result = json.loads(tool_handlers(ingress)['project_work_request']({'request': 'work'}))
    assert result['ok'] is False and result['error'] == 'PermissionError'
    assert 'channel' in result['message'] and 'elsewhere' in result['message']
    assert '!project request' in result['message']


def test_tool_refusal_outside_slack_has_message(env, tmp_path, monkeypatch):
    ingress = make(env, tmp_path)
    install_turn(monkeypatch, PLATFORM='cli')
    result = json.loads(tool_handlers(ingress)['project_work_status']({'workflow_id': 'w'}))
    assert result['error'] == 'PermissionError' and 'Slack' in result['message']


def test_real_bolt_app_runs_plugin_middleware(env, tmp_path, caplog):
    """Drive a real slack_bolt AsyncApp the way the Slack adapter wires it (native.use).

    Bolt's built-in IgnoringSelfEvents drops the bot's own messages BEFORE plugin
    middleware, so a self post (e.g. a restart notice) can never produce a line;
    other bots and humans reach the middleware.
    """
    pytest.importorskip('slack_bolt')
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.authorization import AuthorizeResult
    from slack_bolt.request.async_request import AsyncBoltRequest

    ingress = make(env, tmp_path)
    caplog.set_level('INFO')

    async def authorize(**_):
        return AuthorizeResult(enterprise_id=None, team_id='team', bot_user_id='UBOT',
                               bot_id='BSELF', bot_token='xoxb-test')

    async def main():
        app = AsyncApp(authorize=authorize, request_verification_enabled=False)
        @app.event('message')
        async def _listener(event, say, body):
            return None
        app.use(ingress.middleware)
        for ev in ({'channel': 'channel', 'user': 'stranger', 'ts': 'human', 'text': 'hi'},
                   {'channel': 'elsewhere', 'user': 'UEVE', 'bot_id': 'BEVE', 'ts': 'otherbot'},
                   {'channel': 'channel', 'user': 'UBOT', 'bot_id': 'BSELF', 'ts': 'self'}):
            payload = {'type': 'event_callback', 'team_id': 'team',
                       'event': {'type': 'message', **ev},
                       'authorizations': [{'user_id': 'UBOT', 'team_id': 'team', 'is_bot': True}]}
            response = await app.async_dispatch(AsyncBoltRequest(body=json.dumps(payload),
                                                                 mode='socket_mode'))
            assert response.status == 200
    asyncio.run(main())
    assert 'Project ingress ignored ts=human thread=None user=stranger: not_project_traffic' in caplog.text
    assert 'Project ingress ignored ts=otherbot channel=elsewhere: outside project channel' in caplog.text
    assert 'ts=self' not in caplog.text

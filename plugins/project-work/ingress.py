"""Authenticated Slack ingress. Only request/status are exposed to the model."""
import asyncio
from collections import OrderedDict
from dataclasses import asdict
import json
import re
import threading

from .service import AuthContext


class Ingress:
    def __init__(self, policy, client):
        self.policy, self.client = policy, client
        self.events = OrderedDict()
        self.lock = threading.Lock()

    def observe(self, body):
        """Call ONLY from authenticated native Slack Bolt middleware."""
        ev = body.get('event', {})
        if (body.get('type') != 'event_callback' or body.get('team_id') != self.policy.team_id
                or ev.get('channel') != self.policy.channel_id or ev.get('type') not in ('message', 'app_mention')
                or not ev.get('user') or not ev.get('ts') or ev.get('bot_id') or ev.get('subtype')):
            return None
        ctx = AuthContext('slack', body['team_id'], ev['channel'], ev['ts'],
                          ev.get('thread_ts'), ev['user'], self.policy.ingress_profile)
        key = (ctx.team_id, ctx.channel_id, ctx.message_id, ctx.user_id)
        with self.lock:
            self.events[key] = ctx
            self.events.move_to_end(key)
            while len(self.events) > 2048:
                self.events.popitem(last=False)
        return ctx

    def current(self):
        # Never use get_session_env's process-env fallback as an authentication proof.
        from gateway import session_context as sc
        def value(name):
            v = sc._VAR_MAP['HERMES_SESSION_' + name].get()
            return v if isinstance(v, str) else ''
        if value('PLATFORM') != 'slack' or value('PROFILE') != self.policy.ingress_profile:
            raise PermissionError('Authenticated Slack turn required')
        key = (value('SCOPE_ID'), value('CHAT_ID'), value('MESSAGE_ID'), value('USER_ID'))
        with self.lock:
            ctx = self.events.get(key)
        if ctx is None or (ctx.thread_id or '') != value('THREAD_ID'):
            raise PermissionError('No authenticated Slack event for this turn')
        return ctx

    def tool(self, name, args):
        ctx = self.current()
        required = {'request'} if name == 'project_work_request' else {'workflow_id'}
        if not isinstance(args, dict) or set(args) != required:
            raise ValueError('Only the documented scoped argument is accepted')
        if name == 'project_work_request':
            return self.client.call('request', context=asdict(ctx), text=args['request'])
        return self.client.call('status', context=asdict(ctx), workflow_id=args['workflow_id'])

    async def middleware(self, body, next):
        ctx = self.observe(body)
        if ctx is None:
            return await next()
        text = body['event'].get('text', '')
        # Strip only a leading bot mention, never arbitrary interior content.
        command = re.sub(r'^<@[A-Z0-9]+>\s*', '', text).strip()
        operation, args = None, {}
        if command.startswith('!project request '):
            operation, args = 'request', {'text': command[len('!project request '):]}
        elif command.startswith('!project approve '):
            parts = command.split()
            if len(parts) == 4:
                operation, args = 'approve', {'workflow_id': parts[2], 'plan_hash': parts[3]}
        elif ctx.thread_id:
            # Native observer catches late followups even when the bot isn't mentioned.
            # Unknown threads are ignored by the server, not converted to new workflows.
            operation, args = 'observe_followup', {'text': text}
        if operation:
            await asyncio.to_thread(self.client.call, operation, context=asdict(ctx), **args)
        # Keep native Slack routing/home-channel behavior untouched. Tool calls dedupe
        # the same event if Alpha also decides to request work from it.
        return await next()


def register_tools(ctx, ingress):
    specs = {
        'project_work_request': ('Request work ONLY for the configured project. Identity and targets come from authenticated Slack; never accept repo/profile/path overrides. New work requires owner confirmation of a concrete plan.', 'request'),
        'project_work_status': ('Read the configured project workflow status. This cannot approve, execute, publish or archive.', 'workflow_id'),
    }
    for name, (description, field) in specs.items():
        def handler(args, _name=name, **kwargs):
            try:
                return json.dumps({'ok': True, 'result': ingress.tool(_name, args)})
            except Exception as exc:
                return json.dumps({'ok': False, 'error': type(exc).__name__})
        ctx.register_tool(name=name, toolset='project-work', description=description,
            schema={'name': name, 'description': description, 'parameters': {
                'type': 'object', 'properties': {field: {'type': 'string'}},
                'required': [field], 'additionalProperties': False}}, handler=handler)

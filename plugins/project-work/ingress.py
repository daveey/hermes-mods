"""Authenticated Slack ingress. Only request/status are exposed to the model."""
import logging
from collections import OrderedDict
from dataclasses import asdict
import json
import os
import re
import sys
import threading
import types

from .service import AuthContext

log = logging.getLogger(__name__)

# Names the command without starting with it (e.g. a pasted instruction about the command).
COMMAND_HINT = re.compile(r'!project\b', re.IGNORECASE)
USAGE = ('To request work, send a new message in this channel that starts with '
         '`!project request ` followed by what you want, for example:\n'
         '`!project request Add a one-line comment to README.md`\n'
         'I reply in that thread with a plan and the exact `!project approve <id> <hash>` line to send.')
NOTICES = {
    'not_a_command': "I didn't start anything. This message isn't a `!project` command: "
                     'the command has to be the first thing in the message.\n' + USAGE,
    'empty_request': "I didn't start anything. `!project request` needs a description after it.\n" + USAGE,
    'malformed_approve': "I didn't approve anything. Reply in the plan's thread with the exact "
                         '`!project approve <id> <hash>` line from the plan message, and nothing else.',
}


# ---- reload-stable live state ------------------------------------------------------------------
# Core wires a Slack handler factory ONCE per (plugin, qualname) on a live Bolt client and Bolt
# middleware cannot be removed (gateway/platforms/base.py BasePlatformAdapter._wire_plugin_handlers).
# A reload-plugins (force re-discovery evicts hermes_plugins.<slug>.* from sys.modules, imports it
# again and re-runs register()) therefore leaves the FIRST registration's middleware receiving events
# while the tools come from the NEW registration. Both use one per-profile event store held in a
# sys.modules entry OUTSIDE the evicted namespace, and the wired middleware dispatches to the newest
# registration's Ingress at call time, so its gate, kill switch and queue are always current.
# Schema (version 1; keep stable across plugin versions): stores[home] -> namespace(events, lock),
# current[home] -> newest Ingress (has async ``middleware(body, next, client=None)``).
LIVE_MODULE = '_project_work_live_ingress'
LIVE_VERSION = 1
EVENT_CACHE = 2048


def live_state():
    holder = sys.modules.get(LIVE_MODULE)
    if holder is None:
        fresh = types.ModuleType(LIVE_MODULE)
        fresh.version, fresh.lock, fresh.stores, fresh.current = LIVE_VERSION, threading.Lock(), {}, {}
        holder = sys.modules.setdefault(LIVE_MODULE, fresh)  # atomic: first writer wins
    if getattr(holder, 'version', None) != LIVE_VERSION:
        raise PermissionError('Project work found an unknown live ingress holder in this process; '
                              'restart the gateway')
    return holder


def home_key(home):
    return os.path.realpath(str(home))


def event_store(key):
    """The one authenticated-event store for profile home ``key`` (created once, never replaced)."""
    holder = live_state()
    with holder.lock:
        store = holder.stores.get(key)
        if store is None:
            store = holder.stores[key] = types.SimpleNamespace(events=OrderedDict(), lock=threading.Lock())
        return store


def publish(key, ingress):
    """Make ``ingress`` the newest registration for ``key``; returns the one it replaced (or None)."""
    holder = live_state()
    with holder.lock:
        previous = holder.current.get(key)
        holder.current[key] = ingress
    return previous


def live_middleware(key):
    """Bolt middleware for profile home ``key``: dispatches to the newest registration per event."""
    async def middleware(body, next, client=None):
        try:
            ingress = live_state().current.get(key)
        except Exception:
            ingress = None
        if ingress is None:
            # No live registration: nothing is observed or queued; native routing/ack still runs.
            log.warning('Project ingress refused: no live project-work registration for this profile')
            return await next()
        return await ingress.middleware(body, next, client=client)
    return middleware


class Ingress:
    def __init__(self, policy, client, queue=None, gate=None, ingress_gate=None, store=None):
        self.policy, self.client = policy, client
        self.queue = queue
        # Re-run at every tool call: a skill or mount that appears after register() (a git pull, a
        # hub install) would otherwise go unaudited for the gateway's lifetime.
        self.gate = gate
        # Re-run at every native Slack event, before anything is observed, queued or answered: the
        # same drift (e.g. a docker_forward_env added after register()) must close ingress too, not
        # only the tools. register() binds it to the registered profile's freshly read policy,
        # because the Bolt middleware runs in whatever context the Socket Mode task inherited.
        self.ingress_gate = ingress_gate if ingress_gate is not None else gate
        if store is None:
            store = types.SimpleNamespace(events=OrderedDict(), lock=threading.Lock())
        self.events, self.lock = store.events, store.lock

    def observe(self, body):
        """Call ONLY from authenticated native Slack Bolt middleware."""
        ev = body.get('event', {})
        if (body.get('type') != 'event_callback' or body.get('team_id') != self.policy.team_id
                or ev.get('channel') != self.policy.channel_id or ev.get('type') not in ('message', 'app_mention')
                or not ev.get('user') or not ev.get('ts') or ev.get('bot_id')
                or ev.get('user_team', self.policy.team_id) != self.policy.team_id
                or ev.get('subtype') not in (None, 'file_share', 'thread_broadcast', 'me_message')):
            return None
        ctx = AuthContext('slack', body['team_id'], ev['channel'], ev['ts'],
                          ev.get('thread_ts'), ev['user'], self.policy.ingress_profile)
        key = (ctx.team_id, ctx.channel_id, ctx.message_id, ctx.user_id)
        with self.lock:
            self.events[key] = ctx
            self.events.move_to_end(key)
            while len(self.events) > EVENT_CACHE:
                self.events.popitem(last=False)
        return ctx

    def current(self):
        # Never use get_session_env's process-env fallback as an authentication proof.
        from gateway import session_context as sc
        def value(name):
            v = sc._VAR_MAP['HERMES_SESSION_' + name].get()
            return v if isinstance(v, str) else ''
        platform, chat = value('PLATFORM'), value('CHAT_ID')
        how = ('Ask the requester to post `!project request <what you want>` in the project '
               'channel ' + self.policy.channel_id + ' from their own Slack account.')
        if platform != 'slack' or value('PROFILE') != self.policy.ingress_profile:
            raise PermissionError('Project work only runs in an authenticated Slack turn for '
                                  'profile ' + self.policy.ingress_profile + '; this turn is '
                                  'platform=' + (platform or 'none') + '. ' + how)
        key = (value('SCOPE_ID'), chat, value('MESSAGE_ID'), value('USER_ID'))
        with self.lock:
            ctx = self.events.get(key)
        allowed_threads = {ctx.thread_id} if ctx and ctx.thread_id else {'', ctx.message_id} if ctx else set()
        if ctx is None or value('THREAD_ID') not in allowed_threads:
            if chat != self.policy.channel_id:
                raise PermissionError('Project work only accepts a human message in the project '
                                      'channel ' + self.policy.channel_id + '; this turn is in '
                                      'channel ' + (chat or 'none') + '. ' + how)
            raise PermissionError('No authenticated human Slack message for this turn (bot posts, '
                                  'edits and other workspaces are never accepted). ' + how)
        return ctx

    def tool(self, name, args):
        if self.gate is not None:
            self.gate()
        ctx = self.current()
        required = {'request'} if name == 'project_work_request' else {'workflow_id'}
        if not isinstance(args, dict) or set(args) != required:
            raise ValueError('Only the documented scoped argument is accepted')
        if name == 'project_work_request':
            return self.client.call('request', context=asdict(ctx), text=args['request'])
        return self.client.call('status', context=asdict(ctx), workflow_id=args['workflow_id'])

    async def middleware(self, body, next, client=None):
        notice = None
        try:
            notice = self.enqueue(body)
        except Exception:
            log.exception('Project ingress append failed')
        finally:
            # Native Bolt routing/ack must run even on malformed input or disk failure.
            result = await next()
        if notice is not None:
            await send_notice(client, notice)
        return result

    def classify(self, ctx, text):
        """Return (operation, args, reason). operation None = nothing queued; reason is a log code."""
        # Strip only a leading bot mention, never arbitrary interior content.
        command = re.sub(r'^<@[A-Z0-9]+>\s*', '', text or '').strip()
        if command.startswith('!project request '):
            return 'request', {'text': command[len('!project request '):]}, 'request'
        if command.startswith('!project approve '):
            parts = command.split()
            if len(parts) == 4:
                return 'approve', {'workflow_id': parts[2], 'plan_hash': parts[3]}, 'approve'
        if ctx.thread_id:
            # Native observer catches late followups even when the bot isn't mentioned.
            # Unknown threads are ignored by the server, not converted to new workflows.
            return 'observe_followup', {'text': text}, 'followup'
        if command.startswith('!project approve'):
            return None, {}, 'malformed_approve'
        if command.startswith('!project request'):
            return None, {}, 'empty_request'
        if COMMAND_HINT.search(command):
            return None, {}, 'not_a_command'
        return None, {}, 'not_project_traffic'

    def notice_for(self, ctx, reason):
        """Owner-only usage reply for a top-level near-miss; never for ordinary chat."""
        if reason not in NOTICES or ctx.thread_id or ctx.user_id not in self.policy.owner_ids:
            return None
        return {'channel': ctx.channel_id, 'thread_ts': ctx.message_id, 'text': NOTICES[reason]}

    def log_ignored(self, body):
        """One line per dropped event so a silent ingress is never mistaken for a dead one."""
        ev = body.get('event') if isinstance(body, dict) else None
        if not isinstance(ev, dict) or body.get('type') != 'event_callback':
            log.debug('Project ingress ignored: not an event callback (type=%s)',
                      body.get('type') if isinstance(body, dict) else type(body).__name__)
            return
        # Reactions, presence and other non-message events stay at debug: they are high volume.
        level = logging.INFO if ev.get('type') in ('message', 'app_mention') else logging.DEBUG
        if ev.get('channel') != self.policy.channel_id:
            log.log(level, 'Project ingress ignored ts=%s channel=%s: outside project channel %s '
                    '(type=%s bot=%s)', ev.get('ts'), ev.get('channel'), self.policy.channel_id,
                    ev.get('type'), bool(ev.get('bot_id')))
            return
        log.log(level, 'Project ingress ignored ts=%s: not an authenticated human event '
                '(type=%s subtype=%s bot=%s)', ev.get('ts'), ev.get('type'),
                ev.get('subtype'), bool(ev.get('bot_id')))

    def admit(self):
        """Run the sandbox gate for one native event. False = refused (one WARNING, no side effect)."""
        if self.ingress_gate is None:
            return True
        try:
            self.ingress_gate()
        except Exception as exc:
            # Reason class only: gate messages can name host paths, and the event body is untrusted.
            log.warning('Project ingress refused: sandbox gate failed (%s); event not observed, '
                        'queued or answered', type(exc).__name__)
            return False
        return True

    def enqueue(self, body):
        """Queue an authenticated command. Returns a thread notice to send, or None."""
        # Gate FIRST: a refusal must leave no observe entry, queue row or notice behind.
        if not self.admit():
            return None
        ctx = self.observe(body) if isinstance(body, dict) else None
        if ctx is None:
            self.log_ignored(body)
            return None
        operation, args, reason = self.classify(ctx, body['event'].get('text', ''))
        if operation:
            if self.queue is None:
                raise RuntimeError('Durable ingress queue is required')
            self.queue.append(operation, context=asdict(ctx), **args)
            log.info('Project ingress queued %s ts=%s thread=%s user=%s', operation,
                     ctx.message_id, ctx.thread_id, ctx.user_id)
            return None
        log.info('Project ingress ignored ts=%s thread=%s user=%s: %s', ctx.message_id,
                 ctx.thread_id, ctx.user_id, reason)
        return self.notice_for(ctx, reason)


async def send_notice(client, notice):
    """Post a usage reply in the message's thread; failures are logged, never raised."""
    if client is None:
        log.warning('Project ingress notice not sent (thread=%s): no native Slack client',
                    notice['thread_ts'])
        return None
    try:
        result = await client.chat_postMessage(**notice)
    except Exception:
        log.exception('Project ingress notice failed (thread=%s)', notice['thread_ts'])
        return None
    ts = result.get('ts') if hasattr(result, 'get') else None
    log.info('Project ingress notice sent: thread=%s reply_ts=%s', notice['thread_ts'], ts)
    return ts


def register_tools(ctx, ingress):
    specs = {
        'project_work_request': ('Request work ONLY for the configured project. Identity and targets come from authenticated Slack; never accept repo/profile/path overrides. New work requires owner confirmation of a concrete plan.', 'request'),
        'project_work_status': ('Read the configured project workflow status. This cannot approve, execute, publish or archive.', 'workflow_id'),
    }
    for name, (description, field) in specs.items():
        def handler(args, _name=name, **kwargs):
            try:
                return json.dumps({'ok': True, 'result': ingress.tool(_name, args)})
            except (PermissionError, ValueError, RuntimeError) as exc:
                # Plugin-authored refusals: the model needs the reason to tell the requester.
                return json.dumps({'ok': False, 'error': type(exc).__name__,
                                   'message': str(exc)[:500]})
            except Exception as exc:
                return json.dumps({'ok': False, 'error': type(exc).__name__})
        ctx.register_tool(name=name, toolset='project-work', description=description,
            schema={'name': name, 'description': description, 'parameters': {
                'type': 'object', 'properties': {field: {'type': 'string'}},
                'required': [field], 'additionalProperties': False}}, handler=handler)

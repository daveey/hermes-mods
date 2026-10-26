"""Native Hermes/Slack binding, independent of a deployment's RPC transport.

Inject trusted host-side callables (never model arguments):
  rpc(method, params) -> unwrapped native result
  owner_tool(name, arguments, live_session_id) -> native tool result
  sessions(query) -> normalized exact persisted session rows (with messages)
  cards(card_id) -> native Task fields plus trusted board and parents readback
  projects(query) -> native Project.to_dict plus verified profile and repo
  card_keys(query) -> all matching task IDs, including archived (never truncated)
  slack(method, payload) -> native Slack Web API response for policy.team_id
  evidence(workflow_row) -> freshly collected evidence facts; see verify_evidence

The deployment binds these to its supervised native gateway/owner conversation,
projects store and repository CI reader. This module never edits native databases
or fabricates records. Unknown/missing native state fails closed.
"""
import json
import re
import uuid


class InboundChanged(RuntimeError):
    """Positive thread invalidation, not a transport outage."""


class NativeBridge:
    def __init__(self, policy, *, rpc, owner_tool, sessions, cards, slack, evidence, bot_user_id,
                 projects=None, card_keys=None, quiesce=None):
        self.policy = policy
        self.rpc, self.owner_tool = rpc, owner_tool
        self.sessions, self.cards, self.slack, self.evidence = sessions, cards, slack, evidence
        self.projects, self.card_keys = projects, card_keys
        self.quiesce = quiesce
        self.bot_user_id = bot_user_id
        self.live = {}

    def _bind_live(self, live_id, stored_id):
        # A cold resume replaces the gateway-local ID, not durable ownership.
        self.live = {key: value for key, value in self.live.items()
                     if value != stored_id}
        self.live[live_id] = stored_id

    def _exact(self, stored_id):
        rows = self.sessions({'id': stored_id, 'profile': self.policy.builder_profile})
        if len(rows) != 1 or rows[0].get('id') != stored_id:
            raise ValueError('Exact stored session not found')
        row = rows[0]
        if (row.get('source') != 'desktop' or row.get('profile') != self.policy.builder_profile
                or row.get('cwd') != self.policy.cwd or row.get('parent_session_id') is not None):
            raise PermissionError('Session outside workflow policy')
        return row

    def create_session(self, workflow_id, title, cwd, profile):
        if cwd != self.policy.cwd or profile != self.policy.builder_profile or title != 'Project work ' + workflow_id:
            raise PermissionError('Session target override')
        result = self.rpc('session.create', {'profile': profile, 'source': 'desktop', 'cwd': cwd,
            'title': title, 'hidden': False, 'close_on_disconnect': False,
            'idempotency_key': 'project-work:' + workflow_id})
        self._bind_live(result['session_id'], result['stored_session_id'])
        return result

    def recover_session(self, workflow_id):
        title = 'Project work ' + workflow_id
        rows = self.sessions({'title': title, 'profile': self.policy.builder_profile})
        rows = [r for r in rows if r.get('title') == title]
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError('Ambiguous native session ownership')
        row = self._exact(rows[0]['id'])
        result = self.rpc('session.resume', {'session_id': row['id'], 'profile': self.policy.builder_profile})
        if result.get('stored_session_id') != row['id']:
            raise ValueError('Resume changed durable identity')
        self._bind_live(result['session_id'], row['id'])
        return result

    def _resolve_live(self, live_id):
        stored = self.live.get(live_id)
        if stored:
            return live_id, stored
        # Durable session lookup is the authority after a runtime restart.
        matches = self.sessions({'runtime_id': live_id, 'profile': self.policy.builder_profile})
        if len(matches) != 1:
            raise ValueError('Stale live id: recover_session is required')
        row = self._exact(matches[0]['id'])
        result = self.rpc('session.resume', {'session_id': row['id'], 'profile': self.policy.builder_profile})
        if result.get('stored_session_id') != row['id']:
            raise ValueError('Resume changed durable identity')
        self._bind_live(result['session_id'], row['id'])
        return result['session_id'], row['id']

    def prompt(self, session_id, text, idempotency_key):
        live, stored = self._resolve_live(session_id)
        marker = '[project-work prompt ' + idempotency_key + ']'
        self.rpc('prompt.submit', {'session_id': live, 'text': marker + '\n' + text, 'queued': True})
        if not self.recover_prompt(live, idempotency_key):
            raise RuntimeError('Prompt not yet persisted; reconciliation required')

    def recover_prompt(self, session_id, idempotency_key):
        _, stored = self._resolve_live(session_id)
        row = self._exact(stored)
        marker = '[project-work prompt ' + idempotency_key + ']\n'
        return any(m.get('role') == 'user' and isinstance(m.get('content'), str)
                   and m['content'].startswith(marker) for m in row.get('messages', []))

    def get_session(self, stored_id):
        return self._exact(stored_id)

    def archive(self, stored_id, profile, archived):
        if profile != self.policy.builder_profile:
            raise PermissionError('Profile override')
        row = self._exact(stored_id)
        if not row.get('title', '').startswith('Project work '):
            raise PermissionError('Not a workflow session')
        return self.rpc('session.archive', {'session_key': stored_id, 'profile': profile, 'archived': archived})

    def _card_scope(self, workflow_id, stage, params):
        """Resolve the project in the *owner tool's* profile before any write.

        projects(query) returns a native Project.to_dict(), augmented by trusted
        profile and repo facts (repo verified from primary_path's git remote).
        card_keys(query) returns ALL matching native task IDs, including archived,
        from the requested owner profile and board. It must never use the native
        create tool's active-only idempotency lookup.
        """
        if not callable(self.projects) or not callable(self.card_keys):
            raise RuntimeError('Trusted project lookup and archived-inclusive card recovery required')
        live, stored, marker = self._card_target(workflow_id, stage, params)
        project = self.projects({'project': self.policy.project, 'profile': self.policy.builder_profile})
        if (not isinstance(project, dict) or not project.get('id')
                or self.policy.project not in (project.get('id'), project.get('slug'))
                or project.get('profile') != self.policy.builder_profile
                or project.get('board_slug') != self.policy.board
                or project.get('primary_path') != self.policy.cwd
                or project.get('repo') != self.policy.repo or project.get('archived') is not False):
            raise PermissionError('Native project mapping outside workflow policy')
        return live, stored, marker, project

    def _validate_card(self, card_id, params, scope):
        _, stored, marker, project = scope
        card = self.get_card(card_id)
        expected = {'id': card_id, 'board': self.policy.board, 'project_id': project['id'],
                    'assignee': params['profile'], 'session_id': stored,
                    'parents': params['depends_on'], 'idempotency_key': params['idempotency_key'],
                    'workspace_kind': 'worktree',
                    'workspace_path': self.policy.cwd + '/.worktrees/' + card_id,
                    'completion_contract': self.policy.repo}
        if (not isinstance(card, dict) or any(card.get(k) != v for k, v in expected.items())
                or not (card.get('body') or '').startswith(marker + '\n')):
            raise RuntimeError('Native card scope readback failed')
        return card_id

    def _recover_card(self, params, scope):
        ids = self.card_keys({'idempotency_key': params['idempotency_key'],
                             'board': self.policy.board, 'profile': self.policy.builder_profile,
                             'include_archived': True})
        if not isinstance(ids, list) or len(ids) > 1:
            raise RuntimeError('Ambiguous or incomplete native card recovery')
        return self._validate_card(ids[0], params, scope) if ids else None

    def recover_card(self, workflow_id, stage, params) -> str | None:
        """Read-only recovery after a saved create_intent; never creates on a miss."""
        return self._recover_card(params, self._card_scope(workflow_id, stage, params))

    def _card_target(self, workflow_id, stage, params):
        if stage not in ('implement', 'review', 'release'):
            raise ValueError('Unknown stage')
        expected = {'board': self.policy.board, 'project': self.policy.project,
                    'repo': self.policy.repo, 'cwd': self.policy.cwd,
                    'profile': self.policy.reviewer_profile if stage == 'review' else self.policy.builder_profile}
        if any(params.get(k) != v for k, v in expected.items()):
            raise PermissionError('Card target override')
        stored = params.get('stored_session_id')
        if stored:
            # Card lookup/readback only needs durable ownership, not a live RPC ID.
            live = params.get('session_id')
            if self.live.get(live) != stored:
                live = None
        else:
            # Upgrade old intents by unique durable title, never an expired live ID.
            rows = self.sessions({'title': 'Project work ' + workflow_id,
                                  'profile': self.policy.builder_profile})
            rows = [r for r in rows if r.get('title') == 'Project work ' + workflow_id]
            if len(rows) != 1:
                raise ValueError('Ambiguous durable card owner')
            stored = rows[0]['id']
            live = None
        session = self._exact(stored)
        if session.get('title') != 'Project work ' + workflow_id:
            raise PermissionError('Card owner session mismatch')
        marker = f"project-work:{workflow_id}:{params['generation']}:{stage}"
        return live, stored, marker

    def create_card(self, workflow_id, stage, params):
        """First attempt only. Persisted uncertain intents MUST use recover_card."""
        scope = self._card_scope(workflow_id, stage, params)
        recovered = self._recover_card(params, scope)
        if recovered is not None:
            return recovered
        live, stored, marker, project = scope
        if live is None:
            resumed = self.recover_session(workflow_id)
            if not resumed or resumed['stored_session_id'] != stored:
                raise PermissionError('Card owner resume mismatch')
            live = resumed['session_id']
        args = {'title': f'{stage}: {workflow_id}', 'assignee': params['profile'],
                'session_id': stored, 'workspace_kind': 'worktree',
                'board': params['board'], 'project': project['id'],
                'parents': params['depends_on'], 'idempotency_key': params['idempotency_key'],
                'body': marker + '\nRepository: ' + self.policy.repo + '\nApproved plan:\n' + params['plan'] +
                    '\nRequest:\n' + params['text'] + '\nDo not change workflow scope. Keep evidence for this exact generation and commit.',
                'completion_contract': self.policy.repo}
        result = self.owner_tool('kanban_create', args, live)
        if isinstance(result, str):
            result = json.loads(result)
        card_id = result.get('task_id') or result.get('id') or result.get('task', {}).get('id')
        if not card_id:
            raise RuntimeError('Native kanban_create did not return a task ID')
        return self._validate_card(card_id, params, scope)

    def get_card(self, card_id):
        return self.cards(card_id)

    def quiesce_generation(self, row, old):
        # Trusted deployment must revoke publication authorization, stop workers,
        # and park pending cards via native APIs. A callback is not a model tool.
        if not callable(self.quiesce):
            raise RuntimeError('Native generation retirement adapter required')
        proof = self.quiesce(row, old)
        return (isinstance(proof, dict) and proof.get('workflow_id') == row['workflow_id']
                and proof.get('generation') == old['generation']
                and proof.get('publication_revoked') is True
                and proof.get('live_writer') is False and proof.get('pending_dispatch') is False
                and set(proof.get('idempotency_keys', [])) ==
                    {e['params']['idempotency_key'] for e in old['cards'].values()})

    def verify_evidence(self, row):
        """Facts MUST be refreshed from native task runs, GitHub and delivery now.

        Required facts are deliberately explicit; empty/missing required checks are
        rejected rather than treated as vacuous success. A deployment with no GitHub
        required checks must configure a nonempty agreed check set in its reader.
        """
        data = self.evidence(row)
        sha = data.get('head_sha', '')
        if (not re.fullmatch('[0-9a-f]{40}', sha) or data.get('repo') != self.policy.repo
                or data.get('workflow_id') != row['workflow_id'] or data.get('generation') != row['generation']
                or data.get('pending_followups') is not False or data.get('live_writer') is not False):
            return False
        implement, review = data.get('implementation_run_id'), data.get('review_run_id')
        if not implement or not review or implement == review or data.get('review_head_sha') != sha or data.get('review_approved') is not True:
            return False
        tests, required, checks = data.get('tests'), data.get('required_checks'), data.get('checks')
        if not tests or not required or not isinstance(checks, list):
            return False
        if not all(t.get('sha') == sha and t.get('ok') is True and t.get('command') for t in tests):
            return False
        for name in required:
            matches = [c for c in checks if c.get('name') == name]
            if len(matches) != 1 or matches[0].get('sha') != sha or matches[0].get('conclusion') != 'success' or matches[0].get('status') != 'completed':
                return False
        delivery = data.get('delivery', {})
        if delivery.get('sha') != sha or delivery.get('verified') is not True or not delivery.get('receipt'):
            return False
        previous = None
        for stage in ('implement', 'review', 'release'):
            card = row['evidence'][stage]
            marker = f"project-work:{row['workflow_id']}:{row['generation']}:{stage}"
            if (card.get('status') != 'done' or card.get('board') != self.policy.board
                    or marker not in card.get('body', '') or card.get('parents') != ([previous] if previous else [])):
                return False
            previous = card['id']
        return True

    def _thread(self, row):
        cursor, messages = '', []
        seen = set()
        while True:
            response = self.slack('conversations.replies', {'channel': row['channel_id'], 'ts': row['thread_id'],
                                                           'limit': 200, 'cursor': cursor})
            if response.get('ok') is not True:
                raise RuntimeError('Slack thread read failed')
            messages.extend(response.get('messages', []))
            cursor = response.get('response_metadata', {}).get('next_cursor', '')
            if not cursor:
                if response.get('has_more'):
                    raise RuntimeError('Incomplete Slack pagination')
                return messages
            if cursor in seen:
                raise RuntimeError('Slack pagination loop')
            seen.add(cursor)

    def post_result(self, row, text, idempotency_key):
        response = self.slack('chat.postMessage', {'channel': row['channel_id'], 'thread_ts': row['thread_id'],
            'text': text, 'client_msg_id': str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)),
            'unfurl_links': False, 'unfurl_media': False})
        if response.get('ok') is not True or response.get('channel') != row['channel_id'] or not response.get('ts'):
            raise RuntimeError('Uncertain Slack send')
        return {'channel': response['channel'], 'ts': response['ts']}

    def recover_result(self, row, text, idempotency_key):
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
        matches = [m for m in self._thread(row) if m.get('client_msg_id') == key and m.get('text') == text
                   and m.get('user') == self.bot_user_id and m.get('thread_ts') == row['thread_id']]
        if len(matches) == 1:
            return {'channel': row['channel_id'], 'ts': matches[0]['ts']}
        return None

    def thread_events(self, row):
        """Return [(AuthContext, text)] from a complete authenticated Slack read.

        Includes approval attempts verbatim: the service must route commands before
        ordinary followups, and dedupe by message identity. Edited/deleted or unknown
        human subtypes fail closed rather than silently lose an obligation.
        """
        from decimal import Decimal
        from .service import AuthContext
        if row['channel_id'] != self.policy.channel_id:
            raise PermissionError('Thread outside workflow policy')
        events, seen = [], set()
        for message in self._thread(row):
            if message.get('bot_id') or message.get('user') == self.bot_user_id:
                continue
            ts = message.get('ts')
            if (not ts or not message.get('user') or message.get('edited')
                    or message.get('subtype') not in (None, 'file_share', 'thread_broadcast', 'me_message')
                    or message.get('user_team', self.policy.team_id) != self.policy.team_id
                    or (ts != row['thread_id'] and message.get('thread_ts') != row['thread_id'])
                    or not isinstance(message.get('text'), str) or ts in seen):
                raise InboundChanged('Uncertain native thread event')
            seen.add(ts)
            ctx = AuthContext('slack', self.policy.team_id, row['channel_id'], ts,
                              message.get('thread_ts'), message['user'], self.policy.ingress_profile)
            events.append((ctx, message['text']))
        if not set(row.get('source_ids', [row['message_id']])).issubset(seen):
            raise InboundChanged('Previously ingested source message was deleted')
        return sorted(events, key=lambda event: Decimal(event[0].message_id))

    def _approval(self, messages, row, message_id, user_id, plan_hash):
        if user_id not in self.policy.owner_ids or not message_id or not plan_hash:
            return False
        matches = [m for m in messages if m.get('ts') == message_id]
        if len(matches) != 1:
            return False
        m = matches[0]
        command = re.sub(r'^<@[A-Z0-9]+>\s*', '', m.get('text', '')).strip()
        return (m.get('user') == user_id and m.get('thread_ts') == row['thread_id']
                and not m.get('edited') and not m.get('bot_id')
                and m.get('subtype') in (None, 'file_share', 'thread_broadcast')
                and command == f"!project approve {row['workflow_id']} {plan_hash}")

    def verify_approval(self, ctx, row, plan_hash):
        """Read back the exact unedited owner command; transport errors propagate."""
        if (ctx.platform != 'slack' or ctx.team_id != self.policy.team_id
                or ctx.profile != self.policy.ingress_profile
                or ctx.channel_id != self.policy.channel_id or ctx.channel_id != row['channel_id']
                or ctx.thread_id != row['thread_id'] or plan_hash != row.get('plan_hash')):
            return False
        return self._approval(self._thread(row), row, ctx.message_id, ctx.user_id, plan_hash)

    def read_result(self, row, receipt, text):
        from decimal import Decimal
        if not isinstance(receipt, dict) or receipt.get('channel') != row['channel_id']:
            return False
        messages = self._thread(row)
        if not self._approval(messages, row, row.get('approved_message_id'),
                              row.get('approved_by'), row.get('plan_hash')):
            return False
        matches = [m for m in messages if m.get('ts') == receipt.get('ts')]
        if len(matches) != 1:
            return False
        message = matches[0]
        if (message.get('text') != text or message.get('user') != self.bot_user_id
                or message.get('thread_ts') != row['thread_id'] or message.get('edited')):
            return False
        # Missing source/deleted/edited source is uncertainty, not completion.
        for source_id in row.get('source_ids', [row['message_id']]):
            source = [m for m in messages if m.get('ts') == source_id]
            if len(source) != 1 or source[0].get('edited') or source[0].get('subtype') == 'message_deleted':
                return False
        for m in messages:
            if m.get('user') == self.bot_user_id or m.get('bot_id'):
                continue
            if any(m.get('ts') == a['ts'] and m.get('user') == a['user']
                   and m.get('text') == a['text'] and not m.get('edited')
                   and m.get('thread_ts') == row['thread_id']
                   for a in row.get('approval_attempts', [])):
                continue
            approval = re.sub(r'^<@[A-Z0-9]+>\s*', '', m.get('text', '')).strip()
            if (m.get('ts') == row.get('approved_message_id') and m.get('user') == row.get('approved_by')
                    and approval == f"!project approve {row.get('workflow_id')} {row.get('plan_hash')}"
                    and not m.get('edited')):
                continue
            if (m.get('edited') or m.get('subtype') == 'message_deleted'
                    or m.get('ts') not in row.get('source_ids', [row['message_id']])):
                return False
        return True

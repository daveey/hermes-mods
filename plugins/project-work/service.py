"""Fail-closed orchestration; all native operations are injected trusted ports.

No model-supplied identity, path, profile, board or repository is accepted.
Adapters must implement durable card idempotency, exact evidence verification,
and read_result must inspect the latest inbound thread watermark (not merely
find an outbound message). Unsupported verification MUST raise or return False.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import re
import time
from typing import Protocol

from .store import Store, followup_text
from .native import InboundChanged


@dataclass(frozen=True)
class Policy:
    team_id: str
    channel_id: str
    ingress_profile: str
    builder_profile: str
    reviewer_profile: str
    board: str
    project: str
    repo: str
    cwd: str
    owner_ids: tuple[str, ...]

    def __post_init__(self):
        for field, value in asdict(self).items():
            if field != 'owner_ids' and (not isinstance(value, str) or not value.strip()):
                raise ValueError('Policy fields must be nonempty strings')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repo):
            raise ValueError('repo must be owner/name')
        path = Path(self.cwd)
        if not path.is_absolute() or str(path.resolve()) != self.cwd or not path.is_dir():
            raise ValueError('cwd must be an existing resolved absolute directory')
        if not isinstance(self.owner_ids, (tuple, list)) or not self.owner_ids or any(not isinstance(x, str) or not x for x in self.owner_ids):
            raise ValueError('owner_ids must not be empty')
        if len({self.ingress_profile, self.builder_profile, self.reviewer_profile}) != 3:
            raise ValueError('Ingress, builder and reviewer profiles must be distinct')
        object.__setattr__(self, 'owner_ids', tuple(self.owner_ids))

    def document(self):
        result = asdict(self)
        result['owner_ids'] = list(self.owner_ids)
        return result


@dataclass(frozen=True)
class AuthContext:
    """Only construct in an authenticated transport adapter, never tool arguments."""
    platform: str
    team_id: str
    channel_id: str
    message_id: str
    thread_id: str | None
    user_id: str
    profile: str


class NativePorts(Protocol):
    def create_session(self, workflow_id: str, title: str, cwd: str, profile: str) -> dict: ...
    def recover_session(self, workflow_id: str) -> dict | None: ...
    def prompt(self, session_id: str, text: str, idempotency_key: str): ...
    def recover_prompt(self, session_id: str, idempotency_key: str) -> bool: ...
    def create_card(self, workflow_id: str, stage: str, params: dict) -> str: ...
    def recover_card(self, workflow_id: str, stage: str, params: dict) -> str | None: ...
    def quiesce_generation(self, row: dict, old: dict) -> bool: ...
    def verify_approval(self, ctx: AuthContext, row: dict, plan_hash: str) -> bool: ...
    def thread_events(self, row: dict) -> list: ...
    def get_session(self, stored_id: str) -> dict: ...
    def archive(self, stored_id: str, profile: str, archived: bool): ...
    def get_card(self, card_id: str) -> dict: ...
    def verify_evidence(self, row: dict) -> bool: ...
    def post_result(self, row: dict, text: str, idempotency_key: str): ...
    def recover_result(self, row: dict, text: str, idempotency_key: str): ...
    def read_result(self, row: dict, receipt, text: str) -> bool: ...


class WorkflowError(RuntimeError):
    pass


class Service:
    def __init__(self, policy: Policy, store: Store, ports: NativePorts):
        self.policy, self.store, self.ports = policy, store, ports

    def _authorize(self, ctx):
        if not isinstance(ctx, AuthContext):
            raise PermissionError('Trusted AuthContext required')
        if (ctx.platform != 'slack' or ctx.team_id != self.policy.team_id or
                ctx.channel_id != self.policy.channel_id or ctx.profile != self.policy.ingress_profile or
                not ctx.user_id or not ctx.message_id):
            raise PermissionError('Request outside configured ingress scope')
        if str(Path(self.policy.cwd).resolve()) != self.policy.cwd or not Path(self.policy.cwd).is_dir():
            raise PermissionError('Configured path changed')

    def _row(self, workflow_id):
        if str(Path(self.policy.cwd).resolve()) != self.policy.cwd or not Path(self.policy.cwd).is_dir():
            raise PermissionError('Configured path changed')
        row = self.store.get(workflow_id)
        if row['policy'] != self.policy.document():
            raise PermissionError('Workflow policy mismatch')
        return row

    def status(self, ctx, workflow_id):
        self._authorize(ctx)
        row = self._row(workflow_id)
        if (ctx.thread_id or ctx.message_id) != row['thread_id'] and ctx.user_id not in self.policy.owner_ids:
            raise PermissionError('Status requires workflow thread or owner')
        return row

    def request(self, ctx, text):
        return self._receive(ctx, text, False)

    def publication_gate(self, workflow_id, card_id):
        """Read-only host broker query; never an agent tool or publication lease."""
        with self.store.lock():
            row = self._row(workflow_id)
            member = any(entry.get('id') == card_id for entry in row['cards'].values())
            approved = False
            if row['approved'] and member and row['state'] == 'running':
                ctx = AuthContext('slack', row['team_id'], row['channel_id'],
                                  row['approved_message_id'], row['thread_id'],
                                  row['approved_by'], self.policy.ingress_profile)
                approved = self.ports.verify_approval(ctx, row, row['plan_hash']) is True
                for event, text in self.ports.thread_events(row):
                    self._authorize(event)
                    if (event.message_id != row['approved_message_id']
                            and not self.store.seen(event)):
                        approved = False
            return {'current_generation': row['generation'], 'approved': approved,
                    'card_in_current_generation': member}

    def followup(self, ctx, text):
        return self._receive(ctx, text, True)

    def _receive(self, ctx, text, followup):
        self._authorize(ctx)
        if followup:
            text = followup_text(text)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Nonempty request text required')
        with self.store.lock():
            row, fresh = self.store.receive(asdict(ctx), text, self.policy.document(),
                                            False, followup)
            self._row(row['workflow_id'])
            if fresh:
                self._attempt(row, self._advance)
            return self.store.get(row['workflow_id'])

    def propose_plan(self, workflow_id, plan_text, generation):
        """Trusted controller only; the model has no tool for this mutation."""
        if not isinstance(plan_text, str) or not plan_text.strip():
            raise ValueError('Concrete plan required')
        with self.store.lock():
            row = self._row(workflow_id)
            if generation != row['generation']:
                raise WorkflowError('Stale planning generation')
            if row['state'] != 'awaiting_approval' or row['approved']:
                raise WorkflowError('Plan can only be proposed before approval')
            row['plan'] = plan_text
            row['plan_hash'] = hashlib.sha256(
                f"{workflow_id}:{row['generation']}:{plan_text}".encode()).hexdigest()
            self.store.save(row)
            return row

    def approve(self, ctx, workflow_id, plan_hash):
        self._authorize(ctx)
        if ctx.user_id not in self.policy.owner_ids:
            raise PermissionError('Owner approval required')
        with self.store.lock():
            row = self._row(workflow_id)
            if not row['plan_hash'] or plan_hash != row['plan_hash']:
                raise PermissionError('Approval must bind the current concrete plan hash')
            if self.ports.verify_approval(ctx, row, plan_hash) is not True:
                raise PermissionError('Exact Slack approval readback required')
            if row['approved']:
                return row
            row['approved'] = True
            row['approved_by'] = ctx.user_id
            row['approved_message_id'] = ctx.message_id
            self.store.save(row)
            self._attempt(row, self._advance)
            return self.store.get(workflow_id)

    def _attempt(self, row, operation):
        try:
            operation(row)
        except Exception as exc:
            # Keep the pending operation state for reconciliation, not a fake success.
            current = self.store.get(row['workflow_id'])
            if current['generation'] != row['generation']:
                row = current  # Never overwrite a newer inbound generation.
            row['error'] = type(exc).__name__ + (': ' + str(exc) if isinstance(exc, WorkflowError) else '')
            self.store.save(row)

    def _session(self, row):
        session = self.ports.get_session(row['stored_session_id'])
        expected = {'id': row['stored_session_id'], 'source': 'desktop',
                    'cwd': self.policy.cwd, 'title': row['title'],
                    'profile': self.policy.builder_profile}
        if any(session.get(key) != value for key, value in expected.items()):
            raise WorkflowError('Exact native session ownership check failed')
        if session.get('parent_session_id') is not None or 'archived' not in session:
            raise WorkflowError('Expected a top-level session with archive readback')
        return session

    def _visible(self, row):
        session = self._session(row)
        if session['archived']:
            row['session_state'] = 'unarchive_intent'
            self.store.save(row)
            self.ports.archive(row['stored_session_id'], self.policy.builder_profile, False)
            if self._session(row)['archived']:
                raise WorkflowError('Unarchive readback failed')
        row['session_state'] = 'ready'
        self.store.save(row)

    def _retire(self, row):
        for old in row.get('history', []):
            if old.get('retired'):
                continue
            # Revoke even when a card's create result was lost: resolve and stop
            # by durable keys, not only known IDs.
            if old.get('cards') and self.ports.quiesce_generation(row, old) is not True:
                raise WorkflowError('Earlier generation has not stopped')
            for stage, entry in old.get('cards', {}).items():
                if not entry.get('id'):
                    card_id = self.ports.recover_card(row['workflow_id'], stage, entry['params'])
                    if not card_id:
                        raise WorkflowError('Earlier ambiguous card needs operator recovery')
                    entry.update(id=card_id, state='created')
                    self.store.save(row)
            if old.get('state') == 'post_intent' and not old.get('result_receipt'):
                old['result_receipt'] = self.ports.recover_result(old, old['result_text'],
                    f"{row['workflow_id']}:{old['generation']}:result")
                if old['result_receipt'] is None:
                    raise WorkflowError('Earlier delivery remains uncertain; operator recovery required')
            old['retired'] = True
            self.store.save(row)

    def _advance(self, row):
        if row.get('archive_pending') or (row.get('history') and row.get('stored_session_id')):
            self._visible(row)
            row['archive_pending'] = False
            self.store.save(row)
        self._retire(row)
        if not row['session_id']:
            if row['session_state'] == 'create_intent':
                session = self.ports.recover_session(row['workflow_id'])
                if not session:
                    raise WorkflowError('Ambiguous session creation; operator recovery required')
            else:
                row['session_state'] = 'create_intent'
                self.store.save(row)
                session = self.ports.create_session(row['workflow_id'], row['title'],
                                                     self.policy.cwd, self.policy.builder_profile)
            if not isinstance(session, dict) or not all(isinstance(session.get(k), str) and session[k]
                                                        for k in ('session_id', 'stored_session_id')):
                raise WorkflowError('Invalid native session identifiers')
            row.update(session_id=session['session_id'], stored_session_id=session['stored_session_id'],
                       session_state='ready')
            self.store.save(row)
        else:
            session = self.ports.recover_session(row['workflow_id'])
            if not session or session.get('stored_session_id') != row['stored_session_id']:
                raise WorkflowError('Native resume could not recover the exact owned session')
            row['session_id'] = session['session_id']
            self.store.save(row)
            self._visible(row)
        # Persist the planning conversation BEFORE creating graph nodes.
        prompt_key = f"{row['workflow_id']}:{row['generation']}:prompt"
        if row['prompt_state'] == 'intent':
            if self.ports.recover_prompt(row['session_id'], prompt_key) is not True:
                raise WorkflowError('Ambiguous prompt outcome; operator recovery required')
            row['prompt_state'] = 'sent'
            self.store.save(row)
        if row['prompt_state'] != 'sent':
            row['prompt_state'] = 'intent'
            self.store.save(row)
            self.ports.prompt(row['session_id'],
                'Planning only. Do not execute, publish, create tasks, or archive. '
                'Propose a concrete plan and acceptance tests for owner confirmation.\n\n' + row['text'],
                prompt_key)
            row['prompt_state'] = 'sent'
            self.store.save(row)
        self._visible(row)
        if not row['approved']:
            row.update(state='awaiting_approval', error=None)
            self.store.save(row)
            return
        previous = None
        for stage in ('implement', 'review', 'release'):
            entry = row['cards'].get(stage)
            key = f"{row['workflow_id']}:{row['generation']}:{stage}"
            if not entry or not entry.get('id'):
                # Native create_card MUST durably dedupe this key across restarts.
                params = dict(idempotency_key=key, board=self.policy.board, project=self.policy.project,
                              repo=self.policy.repo, cwd=self.policy.cwd,
                              profile=self.policy.reviewer_profile if stage == 'review' else self.policy.builder_profile,
                              depends_on=[previous] if previous else [], session_id=row['session_id'],
                              stored_session_id=row['stored_session_id'],
                              generation=row['generation'], text=row['text'], plan=row['plan'])
                if entry:
                    card_id = self.ports.recover_card(row['workflow_id'], stage, entry['params'])
                    if not card_id:
                        raise WorkflowError('Ambiguous card creation; operator recovery required')
                else:
                    row['cards'][stage] = {'state': 'create_intent', 'params': params}
                    self.store.save(row)
                    card_id = self.ports.create_card(row['workflow_id'], stage, params)
                if not isinstance(card_id, str) or not card_id:
                    raise WorkflowError('Missing native card identifier')
                if self.ports.get_card(card_id).get('id') != card_id:
                    raise WorkflowError('Native card creation readback failed')
                row['cards'][stage].update(id=card_id, state='created')
                self.store.save(row)
            previous = row['cards'][stage]['id']
        row.update(state='running', error=None)
        self.store.save(row)

    def _evidence(self, row):
        if not row['approved']:
            raise WorkflowError('Current generation lacks owner approval')
        for old in row.get('history', []):
            if not old.get('retired'):
                raise WorkflowError('Earlier generation work is still outstanding')
        if set(row['cards']) != {'implement', 'review', 'release'}:
            raise WorkflowError('Missing workflow stages')
        evidence = {}
        for stage, entry in row['cards'].items():
            card = self.ports.get_card(entry['id'])
            if card.get('id') != entry['id'] or card.get('status') != 'done':
                raise WorkflowError('All exact native cards must be done')
            evidence[stage] = card
        row['evidence'] = evidence
        self.store.save(row)
        # Port must prove independent review run, exact commit HEAD, passing tests,
        # current-generation card ownership/dependencies and release readback.
        if self.ports.verify_evidence(row) is not True:
            raise WorkflowError('Independent exact-head completion evidence missing')

    def closeout(self, workflow_id, result_text):
        if not isinstance(result_text, str) or not result_text.strip():
            raise ValueError('Result text required')
        with self.store.lock():
            row = self._row(workflow_id)
            if row['state'] == 'archived':
                return row
            if row['state'] not in ('running', 'post_intent', 'posted', 'archive_intent'):
                raise WorkflowError('Workflow is not ready for closeout')
            if row['result_text'] is not None and row['result_text'] != result_text:
                raise WorkflowError('Cannot replace a pending delivery')
            self._attempt(row, lambda r: self._close(r, result_text))
            return self.store.get(workflow_id)

    def _close(self, row, text):
        # Compensate before evidence/watermark gates, including restart after an
        # archive RPC applied remotely but timed out locally.
        if row.get('archive_pending') or row['state'] == 'archive_intent':
            self._visible(row)
            row.update(archive_pending=False, state='posted')
            self.store.save(row)
        generation = row['generation']
        self._evidence(row)
        if row['state'] == 'post_intent':
            receipt = self.ports.recover_result(row, text, f"{row['workflow_id']}:{generation}:result")
            if receipt is None:
                raise WorkflowError('Ambiguous delivery outcome; do not repost or archive')
            row.update(result_receipt=receipt, state='posted')
            self.store.save(row)
        if row['state'] == 'running':
            self._visible(row)
            row.update(state='post_intent', result_text=text, result_receipt=None)
            self.store.save(row)
            receipt = self.ports.post_result(row, text, f"{row['workflow_id']}:{generation}:result")
            if receipt is None:
                raise WorkflowError('Missing result receipt')
            row.update(result_receipt=receipt, state='posted')
            self.store.save(row)
        # MUST verify delivery AND refresh remote inbound watermark, including
        # messages not yet dispatched to this process. Any uncertainty is failure.
        if self.ports.read_result(row, row['result_receipt'], text) is not True:
            raise WorkflowError('Delivery or latest inbound watermark not verified')
        if self.store.get(row['workflow_id'])['generation'] != generation:
            raise WorkflowError('New generation invalidates completion')
        session = self._session(row)
        if not session['archived']:
            if session.get('busy') is not False or session.get('pending_approval') is not False:
                raise WorkflowError('Native writer or approval state is not idle')
            row.update(state='archive_intent', archive_pending=True)
            self.store.save(row)
            self.ports.archive(row['stored_session_id'], self.policy.builder_profile, True)
        if not self._session(row)['archived']:
            raise WorkflowError('Archive readback failed')
        try:
            self._evidence(row)
            if (self.ports.read_result(row, row['result_receipt'], text) is not True or
                    self.store.get(row['workflow_id'])['generation'] != generation):
                raise WorkflowError('Late followup invalidates archive')
            session = self._session(row)
            if session.get('busy') is not False or session.get('pending_approval') is not False:
                raise WorkflowError('Native session became active during archive')
        except Exception:
            self._visible(row)
            row['state'] = 'posted'
            self.store.save(row)
            raise
        row.update(state='archived', error=None, archive_pending=False,
                   archived_at=time.time(), archive_check_after=0)
        self.store.save(row)

    def _backfill(self, row):
        # Call under the workflow effect lock. Never call public methods here:
        # they acquire that non-reentrant lock again.
        for ctx, text in self.ports.thread_events(row):
            self._authorize(ctx)
            if self.store.seen(ctx):
                continue
            command = re.sub(r'^<@[A-Z0-9]+>\s*', '', text).strip()
            if command.startswith('!project approve '):
                attempts = row.setdefault('approval_attempts', [])
                if not any(a['ts'] == ctx.message_id for a in attempts):
                    attempts.append({'ts': ctx.message_id, 'user': ctx.user_id, 'text': text})
                parts = command.split()
                if (len(parts) == 4 and parts[2] == row['workflow_id']
                        and parts[3] == row.get('plan_hash') and ctx.user_id in self.policy.owner_ids
                        and self.ports.verify_approval(ctx, row, parts[3]) is True):
                    row.update(approved=True, approved_by=ctx.user_id, approved_message_id=ctx.message_id)
                self.store.save(row)
                self.store.record(ctx, text, row['workflow_id'])
            else:
                row, fresh = self.store.receive(asdict(ctx), text, self.policy.document(), False, True)
                if fresh:
                    self._advance(row)
        return row

    def reconcile(self):
        """Replay only safe pending effects; ambiguity is retained as a visible error."""
        results = []
        for candidate in self.store.rows():
            with self.store.lock():
                # Never apply a new configuration to previously authorized work.
                if candidate['policy'] != self.policy.document():
                    continue
                row = self._row(candidate['workflow_id'])
                if row['state'] == 'archived':
                    now = time.time()
                    # Verified history is not a perpetual Slack polling obligation.
                    # Authenticated live ingress still reopens at any age.
                    if (now - row.get('archived_at', 0) > 86400
                            or now < row.get('archive_check_after', 0)):
                        continue
                    row['archive_check_after'] = now + 300
                    self.store.save(row)
                try:
                    row = self._backfill(row)
                except Exception as exc:
                    row = self._row(candidate['workflow_id'])
                    if row['state'] == 'archived' and isinstance(exc, InboundChanged):
                        row.update(state='invalidated', archive_pending=True)
                        self.store.save(row)
                    if row['state'] != 'archived' and row.get('stored_session_id'):
                        self._attempt(row, self._visible)
                    row['error'] = 'Thread reconciliation blocked: ' + type(exc).__name__
                    self.store.save(row)
                    results.append(row)
                    continue
                if row['state'] == 'archived':
                    def verify_archived(r):
                        # Transport failures retain the proven archive and retry later.
                        valid = self.ports.read_result(r, r['result_receipt'], r['result_text']) is True
                        if not valid:
                            r.update(state='invalidated', archive_pending=True)
                            self.store.save(r)
                            self._visible(r)
                            raise WorkflowError('Archived receipt or approval changed')
                    self._attempt(row, verify_archived)
                    continue
                if row['state'] == 'invalidated':
                    # Do not demand today's repository HEAD for historic delivery.
                    # A fresh human followup starts a newly approved generation.
                    self._attempt(row, self._visible)
                    results.append(self.store.get(row['workflow_id']))
                    continue
                if row['state'] in ('post_intent', 'posted', 'archive_intent'):
                    self._attempt(row, lambda r: self._close(r, r['result_text']))
                elif row['state'] == 'running':
                    # Reconcile missed completion events without inventing work
                    # summaries: native evidence gates the deterministic receipt.
                    def finish(r):
                        self._evidence(r)
                        text = ('Verified project work ' + r['workflow_id'] + '\nRepository: ' +
                                self.policy.repo + '\nCompleted cards: ' +
                                ', '.join(e['id'] for e in r['cards'].values()))
                        self._close(r, text)
                    self._attempt(row, finish)
                elif not (row['state'] == 'awaiting_approval' and row['prompt_state'] == 'sent' and not row['approved']):
                    self._attempt(row, self._advance)
                results.append(self.store.get(row['workflow_id']))
        return results

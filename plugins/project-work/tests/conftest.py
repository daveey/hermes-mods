import importlib.util
from pathlib import Path
import sys
import types
from dataclasses import replace
import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_project_work_core_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
for name in ('store', 'service'):
    spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
Store = sys.modules[PACKAGE + '.store'].Store
core = sys.modules[PACKAGE + '.service']
Policy, AuthContext, Service = core.Policy, core.AuthContext, core.Service


class Ports:
    def __init__(self):
        self.sessions = {}
        self.cards = {}
        self.calls = []
        self.fail = None
        self.evidence_ok = True
        self.watermark_ok = True
        self.archive_effect = True
        self.posts = []

    def create_session(self, workflow_id, title, cwd, profile):
        self.calls.append('create_session')
        self.sessions[workflow_id] = dict(id='stored-' + workflow_id, source='desktop',
            cwd=cwd, title=title, profile=profile, parent_session_id=None, archived=False,
            busy=False, pending_approval=False)
        if self.fail == 'create_session':
            raise TimeoutError('uncertain creation')
        return dict(session_id='live-' + workflow_id, stored_session_id='stored-' + workflow_id)

    def recover_session(self, workflow_id):
        self.calls.append('recover_session')
        if workflow_id in self.sessions:
            return dict(session_id='live-' + workflow_id, stored_session_id='stored-' + workflow_id)

    def get_session(self, stored_id):
        return dict(next(s for s in self.sessions.values() if s['id'] == stored_id))

    def archive(self, stored_id, profile, archived):
        self.calls.append(('archive', stored_id, profile, archived))
        if self.archive_effect:
            next(s for s in self.sessions.values() if s['id'] == stored_id)['archived'] = archived

    def create_card(self, workflow_id, stage, params):
        key = params['idempotency_key']
        self.calls.append(('create_card', key))
        self.cards.setdefault(key, dict(id=key, status='running', **params))
        if self.fail == stage:
            raise TimeoutError('uncertain card create')
        return key

    def prompt(self, session_id, text, idempotency_key):
        self.calls.append(('prompt', idempotency_key))
        if self.fail == 'prompt':
            raise TimeoutError('uncertain prompt')

    def get_card(self, card_id):
        return dict(self.cards[card_id])

    def recover_prompt(self, session_id, idempotency_key):
        return False

    def recover_result(self, row, text, idempotency_key):
        return None

    def verify_evidence(self, row):
        return self.evidence_ok

    def post_result(self, row, text, idempotency_key):
        self.posts.append((idempotency_key, text))
        if self.fail == 'post_result':
            raise TimeoutError('uncertain delivery')
        return {'id': idempotency_key}

    def read_result(self, row, receipt, text):
        if self.fail == 'read_result':
            raise TimeoutError('thread refresh unavailable')
        return self.watermark_ok and (receipt['id'], text) in self.posts

    def done(self):
        for card in self.cards.values():
            card['status'] = 'done'


@pytest.fixture
def env(tmp_path):
    policy = Policy('team', 'channel', 'ingress', 'builder', 'reviewer',
                    'board', 'project', 'owner/repository', str(tmp_path.resolve()), ('owner',))
    ctx = AuthContext('slack', 'team', 'channel', 'message', None, 'owner', 'ingress')
    store = Store(tmp_path / 'ledger.sqlite')
    ports = Ports()
    return Service(policy, store, ports), ctx, ports


def start(service, ctx, text):
    row = service.request(ctx, text)
    if row['state'] == 'awaiting_approval':
        row = service.propose_plan(row['workflow_id'], 'Implement the request and run its tests.')
        row = service.approve(ctx, row['workflow_id'], row['plan_hash'])
    return row

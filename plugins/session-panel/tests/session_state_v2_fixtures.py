"""Public core collector -> Feed.compute -> reducer fixtures (no classifier doubles)."""
from __future__ import annotations
import contextlib
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
BASELINE = Path(__file__).with_name('session_state_v2_goldens.json')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def core_source():
    core = Path(os.environ.get('HERMES_CORE_SOURCE', ROOT.parent / 'core'))
    return core if (core / 'tests/fixtures/session-state-v2/scenarios.json').is_file() else None


def presented_request(card, events, home):
    """Use the real mirror request factory, ActionStore and presentation binding."""
    core = load('_session_v2_user_steps', ROOT / 'plugins/user-steps/core.py')
    module = load('_session_v2_mirror', ROOT / 'plugins/user-steps/kanban_mirror.py')
    store = core.ActionStore(home / 'actions.sqlite3')
    mirror = module.Mirror(core, store, boards=[], session_exists=lambda _: True, tip=lambda s: s)
    owner = {'gatewayId': 'install:fixture-owner', 'profile': card['profile'],
             'storedSessionId': card['session_id']}
    snap = {'task': card['id'], 'title': card['title'], 'ask': card['human_request'],
            'lifecycle': events}
    request = mirror._request(owner, card['board'], snap)
    mirror._publish(owner, card['board'], snap, request)
    shown = mirror.present(store.get(owner, request['requestId']))
    assert shown.get('presentation'), 'must exercise actual mirror presentation'
    return shown


class Readers:
    """Only source IO injection; the Feed and all classification remain real."""
    def __init__(self, case, now, home):
        self.case, self.now = case, now
        self.tasks = deepcopy(case['cards'])
        self.events = []
        card = case['card']
        if card:
            episodes = case['input'].get('episodes')
            if episodes:
                self.events = [{'kind': kind, 'payload': {}, 'created_at': now - (len(episodes)-i)*60}
                               for i, kind in enumerate(episodes)]
            else:
                self.events = [{'kind': 'status', 'payload': {'status': card['status']},
                                'created_at': now-case['input'].get('triage_age_s', 60)}]
        self.requests = [presented_request(card, self.events, home)] if card and card.get('human_request') else []

    def lineage(self, session):
        return [session]

    def linked(self, lineage):
        return deepcopy(self.tasks)

    def done(self, lineage):
        card = self.case['card']
        return [deepcopy(card)] if card and card['status'] == 'done' else []

    def card(self, cid, board):
        row = next((t for t in self.tasks if t['id'] == cid), None)
        if row is None:
            row = self.case['input'].get('producer')
        if not row or row['id'] != cid:
            return None
        return {'board': board, 'detail': {'task': deepcopy(row), 'events': deepcopy(self.events),
                                           'links': {'parents': [], 'children': []}}}

    def panel_inputs(self, session, lineage):
        row = self.case['session_row']
        runtime = self.case['input'].get('runtime') or {}
        inventories = {'subagents': [], 'processes': []}
        for obs in row.get('execution_observations', []):
            identity = obs.get('identity') or {}
            kind = identity.get('kind')
            if kind in ('subagent', 'process'):
                inventories['subagents' if kind == 'subagent' else 'processes'].append({
                    'id': identity.get('work_id'), 'work_id': identity.get('work_id'),
                    'title': runtime.get('title'), 'gateway': identity.get('gateway'),
                    'profile': identity.get('profile'), 'execution_observations': [deepcopy(obs)]})
        return {'outcome_json': deepcopy(self.case['outcome']), 'requests': self.requests,
                'gateway': 'fixture-gateway', 'gateway_aliases': ['install:fixture-owner'],
                'profile': 'default', 'rentals': [], 'input_errors': [], **inventories}


def run_all():
    source = core_source()
    if source is None:
        return {'skip': 'compatible core shared fixtures unavailable'}
    sys.path.insert(0, str(source))
    shared = load('_session_v2_shared', source / 'tests/hermes_state/session_state_v2_fixtures.py')
    actual = shared.run_all()  # Exactly one collector invocation for the entire manifest.
    feed = load('_session_v2_feed', ROOT / 'plugins/session-panel/dashboard/feed.py')
    result = {'schema_version': 1, 'now': actual['now'], 'coverage': [], 'cases': {}}
    with tempfile.TemporaryDirectory(prefix='panel-session-v2-', dir=os.environ.get('TMPDIR')) as temp:
        with patch('time.time', return_value=actual['now']):
            for case in actual['cases']:
                home = Path(temp) / case['id']
                home.mkdir()
                readers = Readers(case, actual['now'], home)
                bundle = feed.Feed(readers, emit=lambda _: None, clock=lambda: actual['now']).compute('fixture-chat')
                model = bundle['model']
                result['coverage'].append(case['id'])
                result['cases'][case['id']] = {'model': model, 'requests': readers.requests,
                    'tasks': bundle['tasks'], 'session_row': case['session_row'],
                    'core_status': case['outcome']['status'],
                    'runtime_evidence': case['session_row'].get('execution_observations', [])}
    return result


if __name__ == '__main__':
    with contextlib.redirect_stdout(sys.stderr):
        result = run_all()
    print(json.dumps(result, sort_keys=True))

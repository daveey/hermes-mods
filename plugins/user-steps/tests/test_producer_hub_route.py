"""On the hub, every stale_watch card is pool work: hub:write, no machine pin, no home-pin sentence (t_01a1d0e68acaba94).

stale_watch used to file every detector card with ``--runs-on machine:<home gateway>`` (machine:hub on the hub) and
the sentence "Pinned to the gateway that filed it: ...", and the old-ask review card with placement='local'. Hub
admission once ADMITTED a card on that very sentence (t_01a1cf7f8f211403), and a worker reaches the hub's action
store with `ssh hermes-hub` reads and `hub-admin '<command>'`. On a REMOTE_GATEWAYS gateway (the hub) the cards now
declare hub:write (the access core's Board places onto a capability:hub-write worker; hub:read it cannot place), carry
no pin and route their hub steps through hub-admin, hub paths inside the evidence fence. Other gateways keep the pin.

Each test drives the producer's real filing code (``create_card`` with a recording ``subprocess.run``; the review
card dict that goes to the kanban_create tool) and runs core's REAL ``portable_reason`` on a fake hub pool whose
workers attest capability:hub-write (and a control pool that attests nothing), plus core's real hub-admission
``apply()`` with a judge that fails the test if it is consulted.
"""
import argparse
import importlib.util
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOSTILE = '~/.hermes/kanban.db was locked; ssh to the hub and the cron job failed'
NOW = 1_800_000_000.0
POOL = ['worker-2', 'worker-3']
_FENCED = re.compile(r'<!-- placement:evidence -->.*?<!-- /placement:evidence -->', re.DOTALL)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sw = load('up_stale_watch_hub_route', ROOT / 'stale_watch.py')
pe = sw.placement_evidence
PIN_PREFIX = 'Pinned to the gateway that filed it'


def outside(body):
    return _FENCED.sub('', body)


@pytest.fixture
def core(monkeypatch):
    tree = Path(os.environ.get('HERMES_CORE_DIR') or Path.home() / '.hermes' / 'hermes-agent')
    if str(tree) not in sys.path:
        sys.path.insert(0, str(tree))
    try:
        from hermes_cli import kanban_placement as kp
        from hermes_cli import kanban_worker_node as wn
        from hermes_cli import kanban_hub_admission as judge
        from hermes_cli import kanban_hub_admission_placement as hap
        from hermes_cli import kanban_parser
    except ImportError as exc:
        pytest.skip(f'hermes-agent at {tree} lacks hub admission: {exc}')
    if os.environ.get('HERMES_KANBAN_DB') or os.environ.get('HERMES_KANBAN_BOARD'):
        monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
        monkeypatch.delenv('HERMES_KANBAN_BOARD', raising=False)
    state = SimpleNamespace(calls=[], labels=['capability:hub-write'])

    def registry():
        seen = time.time() * 1000
        return {'machines': {w: {'state': 'active', 'pickup': 'explicit', 'labels': list(state.labels),
                                 'last_seen': seen} for w in POOL}}

    monkeypatch.setattr(wn, 'registry', registry)
    monkeypatch.setattr(kp.reg, 'self_gateway', lambda: 'hub')

    def admit(fields, *, hub_reason=None):
        state.calls.append(fields.get('title'))
        raise AssertionError(f"hub admission judge consulted for {fields.get('title')!r}")

    monkeypatch.setattr(judge, 'admit', admit)
    cfg = {'worker_pools': {'hub': list(POOL)}}

    def reason(title, body, requires, labels=('capability:hub-write',)):
        state.labels = list(labels)
        return kp.portable_reason({'title': title, 'body': body, 'assignee': 'default', 'requires': list(requires)},
                                  dict(cfg))

    root = argparse.ArgumentParser()
    kanban_parser.build_parser(root.add_subparsers(dest='cmd'))

    monkeypatch.setattr(kp, 'config', lambda: dict(cfg))

    def decide(argv, body):
        # `hermes kanban create`'s own preflight: worker-first decision + admission, before any write.
        a = root.parse_args(list(argv[1:]))
        state.labels = ['capability:hub-write']
        return hap.cli_plan(a, body, 'scratch', None, None, assignee=a.assignee)

    state.reason, state.decide = reason, decide
    return state


@pytest.fixture
def gateway(monkeypatch):
    """Core's enrollment answer, replaced: a synced board whose configured gateway is ``state['gateway']``."""
    state = {'gateway': 'hub', 'error': None}

    def _core_identity():
        if state['error']:
            raise RuntimeError(state['error'])
        return True, state['gateway']

    monkeypatch.setattr(pe, '_core_identity', _core_identity)
    return state


def _detector_cards():
    item = {'requestId': 'req-1', 'session': HOSTILE, 'question': f'Q: {HOSTILE}', 'reply': f'R: {HOSTILE}'}
    return {
        'stale': sw.card(item),
        'dup': sw.dup_card({'namespace': 'ns', 'session': HOSTILE, 'groups': [['r1', 'r2']]}),
        'xdup': sw.cross_dup_card({'namespace': 'ns', 'ids': ['r1', 'r2'], 'sessions': [HOSTILE, 's2']}),
        'failed': sw.failed_decision_card({'operationId': 'op-12345678', 'failedAt': NOW, 'requestId': 'r1',
                                           'session': HOSTILE, 'userRowId': 7}),
        'undelivered': sw.undelivered_card({
            'operationId': 'op-87654321', 'requestId': 'r1', 'title': f'Approve {HOSTILE}', 'effect': 'approve',
            'choiceId': 'approve', 'recordedAt': '2026-01-01T00:00:00+00:00', 'owner': {'storedSessionId': 's'},
            'attempts': 25, 'ownerWaits': 0, 'busyRefusals': 0, 'dispatchState': 'recorded',
            'lastError': {'class': 'RuntimeError', 'message': HOSTILE, 'at': 'x'}}),
    }


def _review_item():
    return {'requestId': 'r1', 'title': HOSTILE, 'askedAt': NOW - 99 * 3600, 'revision': 1,
            'owner': {'profile': 'default', 'storedSessionId': 's', 'gatewayId': 'g'},
            'chat': {'status': 'found', 'title': HOSTILE, 'lastTurnAt': NOW}, 'cards': [], 'problems': []}


def _filed(monkeypatch, c):
    seen = []
    monkeypatch.setattr(sw.subprocess, 'run', lambda cmd, **k: seen.append((cmd, k['input'])) or
                        SimpleNamespace(returncode=0, stdout='{}', stderr=''))
    sw.create_card(c)
    (cmd, body), = seen
    return cmd, body


def _requires(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == '--requires']


@pytest.mark.parametrize('name', sorted(_detector_cards()))
def test_hub_detector_card_is_hub_write_pool_work(monkeypatch, gateway, core, name):
    c = _detector_cards()[name]
    argv, body = _filed(monkeypatch, c)
    assert '--runs-on' not in argv and not any('machine:' in a for a in argv), argv
    assert 'hub:write' in _requires(argv) and 'hub:read' not in _requires(argv)
    # The producer's self-assertion is gone; the route it names instead is hub-admin / ssh hermes-hub.
    assert PIN_PREFIX not in body and pe.HOME_PIN_PREFIX not in body
    assert 'hub-admin' in outside(body) and 'ssh hermes-hub' in outside(body)
    # Hub paths sit inside the fence; the incident paste stays fenced.
    assert '~/.hermes/logs' not in outside(body) and "hub-admin 'python3 " + sw.SCRIPT in body
    assert HOSTILE not in outside(body)
    # Core's real verdict on a capability:hub-write pool: portable. Control: a pool attesting nothing cannot take it.
    assert core.reason(c['title'], body, _requires(argv)) is None
    assert core.reason(c['title'], body, _requires(argv), labels=()) is not None
    # And core's real hub admission lets it through without consulting the judge.
    out = core.decide(argv, body)
    assert out.get('where') != 'refuse' and 'hub_admission' not in out and core.calls == [], out


def test_hub_review_card_is_hub_write_pool_work(core):
    c = sw.review_card([_review_item()], NOW, gateway='hub', remote=True)
    assert c['placement'] == 'auto' and 'hub:write' in c['requires'] and 'hub:read' not in c['requires']
    body = c['body']
    assert PIN_PREFIX not in body
    assert "hub-admin 'python3 " + sw.SCRIPT + ' --withdraw <id>' in body
    assert "hub-admin 'python3 " + sw.SCRIPT + ' --reviewed <id>' in body
    assert sw.SCRIPT not in outside(body) and 'on this host' not in body
    assert '--withdraw <id>' not in outside(body) and 'Withdraw it only with evidence' in outside(body)
    assert core.reason(c['title'], body, c['requires']) is None
    assert core.reason(c['title'], body, c['requires'], labels=()) is not None


def test_review_old_asks_routes_by_gateway(monkeypatch, gateway):
    made = []
    monkeypatch.setattr(sw, 'old_asks', lambda *a, **k: [{**_review_item(), 'request': {}}])
    monkeypatch.setattr(sw, 'chat_facts', lambda *a, **k: {'status': 'found'})
    monkeypatch.setattr(sw, 'linked_cards', lambda *a, **k: ([], []))
    monkeypatch.setattr(sw, '_self_gateway', lambda: gateway['gateway'])
    sw.review_old_asks(None, None, now=NOW, create=made.append, owner_db=lambda o: None, boards=[])
    assert made[-1]['placement'] == 'auto' and 'hub:write' in made[-1]['requires']
    gateway['gateway'] = 'gw-board-a'
    sw.review_old_asks(None, None, now=NOW, create=made.append, owner_db=lambda o: None, boards=[])
    assert made[-1]['placement'] == 'local' and 'hub:write' not in made[-1]['requires']
    assert pe.HOME_PIN_PREFIX in made[-1]['body']


def test_other_gateways_keep_the_machine_pin(monkeypatch, gateway):
    gateway['gateway'] = 'gw-board-a'
    argv, body = _filed(monkeypatch, _detector_cards()['dup'])
    assert argv[argv.index('--runs-on') + 1] == 'machine:gw-board-a' and 'hub:write' not in _requires(argv)
    assert pe.HOME_PIN_PREFIX in body


def test_unreadable_identity_still_files_nothing(monkeypatch, gateway):
    gateway['error'] = 'no hub key'
    seen = []
    monkeypatch.setattr(sw.subprocess, 'run', lambda cmd, **k: seen.append(cmd))
    with pytest.raises(RuntimeError, match='refusing to file'):
        sw.create_card(_detector_cards()['stale'])
    assert seen == []

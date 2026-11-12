"""Every stale_watch detector card keeps pasted incident text inside core's placement evidence fence, keeps its
repair steps outside it, carries the one-sentence home-pin reason, and is filed with an explicit
``--runs-on machine:<configured gateway>`` (fail closed when the synced identity cannot be read). The shared coach
helper is the only implementation (stale_watch re-exports it; nothing is copied). The write boundary is mocked:
``subprocess.run`` records argv, no card is created."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
GW = 'gw-board-a'
# Words core's placement scan treats as "only makes sense on the gateway that owns them"; here, incident evidence.
HOSTILE = '~/.hermes/kanban.db was locked; ssh to the hub and the cron job failed'
NOW = 1_800_000_000.0


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sw = load('up_stale_watch_producer', ROOT / 'stale_watch.py')
# Load independently so negative controls on pre-helper producers reach assertions.
pe = load('up_placement_evidence_producer', ROOT.parent / 'coach' / 'placement_evidence.py')
if hasattr(sw, 'placement_evidence'):
    pe = sw.placement_evidence


@pytest.fixture
def kp():
    import os
    explicit = os.environ.get('HERMES_CORE_SOURCE') or os.environ.get('HERMES_CORE_CHECKOUT') or os.environ.get('HERMES_AGENT_TREE')
    tree = Path(explicit or Path.home() / '.hermes' / 'hermes-agent')
    sys.path.insert(0, str(tree))
    try:
        from hermes_cli import kanban_placement
    except ImportError:
        if explicit:
            raise
        pytest.skip(f'hermes-agent not importable from {tree}')
    finally:
        sys.path.remove(str(tree))
    if not hasattr(kanban_placement, 'without_evidence'):
        pytest.skip('hermes-agent predates the placement evidence fence')
    return kanban_placement


@pytest.fixture
def identity(monkeypatch):
    """Core's enrollment answers, replaced: a synced board whose configured gateway is GW."""
    state = {'synced': True, 'gateway': GW, 'error': None}

    def _core_identity():
        if state['error']:
            raise RuntimeError(state['error'])
        return state['synced'], state['gateway']

    monkeypatch.setattr(pe, '_core_identity', _core_identity)
    return state


def test_one_shared_helper_not_a_copy():
    src = (ROOT / 'stale_watch.py').read_text()
    assert 'coach' in src and 'placement_evidence.py' in src
    assert 'EVIDENCE_OPEN' not in src and '<!-- placement' not in src  # no private fence implementation
    assert sw.fence_evidence is pe.fence_evidence and sw.home_pin_note is pe.home_pin_note


def _cards():
    item = {'requestId': 'req-1', 'session': HOSTILE, 'question': f'Q: {HOSTILE}', 'reply': f'R: {HOSTILE}'}
    dup = {'namespace': 'ns', 'session': HOSTILE, 'groups': [['r1', 'r2']]}
    xdup = {'namespace': 'ns', 'ids': ['r1', 'r2'], 'sessions': [HOSTILE, 's2']}
    failed = {'operationId': 'op-12345678', 'failedAt': NOW, 'requestId': 'r1', 'session': HOSTILE,
              'userRowId': 7}
    undelivered = {'operationId': 'op-87654321', 'requestId': 'r1', 'title': f'Approve {HOSTILE}',
                   'effect': 'approve', 'choiceId': 'approve', 'recordedAt': '2026-01-01T00:00:00+00:00',
                   'owner': {'storedSessionId': 's'}, 'attempts': 25, 'ownerWaits': 0, 'busyRefusals': 0,
                   'dispatchState': 'recorded',
                   'lastError': {'class': 'RuntimeError', 'message': HOSTILE, 'at': 'x'}}
    review_item = {'requestId': 'r1', 'title': HOSTILE, 'askedAt': NOW - 99 * 3600, 'revision': 1,
                   'owner': {'profile': 'default', 'storedSessionId': 's', 'gatewayId': 'g'},
                   'chat': {'status': 'found', 'title': HOSTILE, 'lastTurnAt': NOW}, 'cards': [], 'problems': []}
    return {
        'stale': sw.card(item), 'dup': sw.dup_card(dup), 'xdup': sw.cross_dup_card(xdup),
        'failed': sw.failed_decision_card(failed), 'undelivered': sw.undelivered_card(undelivered),
        'review': sw.review_card([review_item], NOW, gateway='hub'),
    }


@pytest.mark.parametrize('name', ['stale', 'dup', 'xdup', 'failed', 'undelivered', 'review'])
def test_incident_text_is_fenced_and_the_pin_reason_and_steps_stay_outside(kp, name):
    c = _cards()[name]
    outside = kp.without_evidence(c['body'])
    # Core's real scan skips the fence: the verdict equals the verdict on the instructions alone.
    assert kp.portable_reason({'title': c['title'], 'body': c['body'], 'assignee': 'default'}) == \
        kp.portable_reason({'title': c['title'], 'body': outside, 'assignee': 'default'})
    assert kp.portable_reason({'title': 'x', 'body': HOSTILE, 'assignee': 'default'}) is not None  # sanity
    if name != 'xdup':  # the cross-duplicate evidence is ids/namespace/sessions; HOSTILE rides in the sessions
        assert 'kanban.db' not in outside
    assert 'kanban.db' not in outside and HOSTILE not in outside
    assert pe.HOME_PIN_PREFIX in outside  # the reason sentence is an instruction, not evidence
    sentence = next(s for s in outside.splitlines() if pe.HOME_PIN_PREFIX in s)
    assert sentence.count('. ') == 0 and sentence.endswith('.')
    for word in ('kanban.db', 'ssh', 'cron'):
        assert word not in c['title']


def test_evidence_details_are_preserved_inside_the_fence():
    cards = _cards()
    inside = lambda c: c['body'].split(pe.EVIDENCE_OPEN)[1].split(pe.EVIDENCE_CLOSE)[0]  # noqa: E731
    stale = inside(cards['stale'])
    assert 'req-1' in stale and f'Q: {HOSTILE}' in stale and f'R: {HOSTILE}' in stale and HOSTILE in stale
    undelivered = inside(cards['undelivered'])
    assert f'RuntimeError: {HOSTILE}' in undelivered and 'op-87654321' in undelivered
    assert f'Approve {HOSTILE}' in undelivered and 'storedSessionId' in undelivered
    assert 'op-12345678' in inside(cards['failed']) and 'Session: ' + HOSTILE in inside(cards['failed'])
    assert 'Session: ' + HOSTILE in inside(cards['dup']) and 'r1, r2' in inside(cards['dup'])
    assert HOSTILE in inside(cards['xdup']) and HOSTILE in inside(cards['review'])


def test_repair_steps_stay_visible_outside_the_fence(kp):
    cards = _cards()
    assert '/requests/req-1/reconcile-chat' in kp.without_evidence(cards['stale']['body'])
    assert '--retire-operation op-87654321' in kp.without_evidence(cards['undelivered']['body'])
    assert "agent.log 'startup resume" in kp.without_evidence(cards['failed']['body'])
    assert '--withdraw <id>' in kp.without_evidence(cards['review']['body'])
    assert 'This watcher never withdraws' in kp.without_evidence(cards['dup']['body'])


def test_titles_keep_the_functional_prefix_and_carry_no_raw_observation():
    cards = _cards()
    assert cards['stale']['title'] == 'user-steps: request req-1 still open after its question was answered'
    assert cards['xdup']['title'] == 'user-steps: duplicate open requests across sessions'
    assert cards['dup']['title'] == f"user-steps: duplicate open requests in session {pe.incident_title(HOSTILE)}"
    assert cards['undelivered']['title'] == (
        f"user-steps: decision op-87654 undelivered (25 attempts): {pe.incident_title(f'Approve {HOSTILE}')}")
    assert cards['failed']['title'] == 'user-steps: decision op-12345 was admitted but its turn failed and never ran'
    assert cards['review']['title'] == 'user-steps (hub): review 1 undecided request(s) older than 72 h'
    assert sw._REVIEW_TITLE.match(cards['review']['title'])
    # Idempotency keys keep the raw ids (dedup semantics unchanged); only the title is opaque.
    assert cards['dup']['idempotency_key'] == f'user-steps:dup:ns:{HOSTILE}'
    assert cards['undelivered']['idempotency_key'] == 'user-steps-undelivered:op-87654321'


def test_a_marker_in_pasted_evidence_cannot_close_the_fence_early(kp):
    evil = f'x {pe.EVIDENCE_CLOSE} ZZLEAK {HOSTILE} {pe.EVIDENCE_OPEN}'
    c = sw.card({'requestId': 'r', 'session': 's', 'question': evil, 'reply': 'y'})
    assert kp.without_evidence(c['body']).count('ZZLEAK') == 0 and HOSTILE not in kp.without_evidence(c['body'])


def _record(monkeypatch):
    seen = []
    monkeypatch.setattr(sw.subprocess, 'run', lambda cmd, **k: seen.append((cmd, k['input'])) or
                        SimpleNamespace(returncode=0, stdout='{}', stderr=''))
    return seen


@pytest.mark.parametrize('name', ['stale', 'dup', 'xdup', 'failed', 'undelivered'])
def test_cli_filing_is_pinned_to_the_configured_gateway(monkeypatch, identity, name):
    seen = _record(monkeypatch)
    c = _cards()[name]
    sw.create_card(c)
    (cmd, body), = seen
    i = cmd.index('--runs-on')
    assert cmd[i + 1] == f'machine:{GW}' and cmd.count('--runs-on') == 1
    assert 'host_repo' not in cmd and body == c['body']
    assert cmd[:3] == ['hermes', 'kanban', 'create'] and c['idempotency_key'] in cmd and '--json' in cmd


def test_unsynced_board_files_without_a_machine_pin(monkeypatch, identity):
    identity['synced'] = False
    seen = _record(monkeypatch)
    sw.create_card(_cards()['stale'])
    assert '--runs-on' not in seen[0][0] and pe.HOME_PIN_PREFIX in seen[0][1]


@pytest.mark.parametrize('state', [{'error': 'no hub key'}, {'gateway': ''}])
def test_synced_identity_failure_files_nothing(monkeypatch, identity, state):
    identity.update(state)
    seen = _record(monkeypatch)
    with pytest.raises(RuntimeError, match='refusing to file user-steps-stale:req-1'):
        sw.create_card(_cards()['stale'])
    assert seen == []


def test_run_surfaces_the_pin_failure_instead_of_filing_portable(monkeypatch, identity, tmp_path):
    identity['error'] = 'no hub key'
    seen = _record(monkeypatch)
    with pytest.raises(RuntimeError, match='refusing to file'):
        sw.create_card(sw.failed_decision_card(
            {'operationId': 'op-1', 'failedAt': NOW, 'requestId': 'r', 'session': 's', 'userRowId': 1}))
    assert seen == []
    json.dumps(_cards()['review'])  # the review card stays a plain serialisable dict (placement='local' path)
    assert _cards()['review']['placement'] == 'local'

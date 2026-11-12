"""stale_watch: one refused card never stops the other duties (self-heal cron-d1d80dc84a).

Hub cron 'user-steps stale/duplicate ask watch' exited 1 on 10 straight runs: run() raised at the first card the board
refused (a hub_admission_denied shape the earlier overlap-only fix c1ae5ead never saw), so every later duty (duplicate,
decision, old-ask review cards) was skipped for ~5.5 h. The rule tested here is shape-independent: any exception from
one create (or one detector source) is recorded as ``error:<kind>:<key>: ...`` and the pass continues; main() still
prints every error and exits 1 so the cron streak alert keeps firing.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0
REFUSAL = 'kanban_create failed: {"code": "brand_new_refusal_shape", "error": "never seen before"}'


def load(name):
    spec = importlib.util.spec_from_file_location('iso_' + name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sw = load('stale_watch')


class Chat:
    @staticmethod
    def answered_open(core, store, db):
        return [{'requestId': 'req-1', 'replyAt': 0.0, 'session': 's', 'question': 'q', 'reply': 'r'},
                {'requestId': 'req-2', 'replyAt': 0.0, 'session': 's', 'question': 'q', 'reply': 'r'}]

    @staticmethod
    def duplicate_open(core, store):
        return [{'namespace': 'ns', 'session': 's1', 'groups': [['r1', 'r2']]}]

    @staticmethod
    def cross_session_duplicates(core, store):
        return [{'namespace': 'ns', 'ids': ['r3', 'r4'], 'sessions': ['s1', 's2']}]


def _sources(monkeypatch):
    monkeypatch.setattr(sw, 'failed_decisions', lambda *a, **k: [
        {'operationId': 'op-failed01', 'failedAt': NOW, 'requestId': 'r1', 'session': 's', 'userRowId': 7}])
    monkeypatch.setattr(sw, 'undelivered_decisions', lambda *a, **k: [{
        'operationId': 'op-undeliv1', 'requestId': 'r1', 'title': 'Approve', 'effect': 'approve',
        'choiceId': 'approve', 'recordedAt': '2026-01-01T00:00:00+00:00', 'owner': {'storedSessionId': 's'},
        'attempts': 25, 'ownerWaits': 0, 'busyRefusals': 0, 'dispatchState': 'recorded', 'lastError': None}])
    monkeypatch.setattr(sw, 'dead_chat_asks', lambda *a, **k: [{
        'board': 'default', 'task': 't_1', 'title': 'x', 'session': '20261111_032644_40d5be',
        'blockedAt': NOW - 7200, 'lastTurnAt': None, 'found': False, 'action': 'From owner chat ...'}])
    monkeypatch.setattr(sw, 'home_pin_note', lambda *a, **k: '')
    monkeypatch.setattr(sw, '_local_gateway', lambda core: 'gw')
    reviewed = []
    monkeypatch.setattr(sw, 'review_old_asks',
                        lambda core, store, now=None, create=None, **k: reviewed.append(create) or ['r9'])
    return reviewed


def _first_refused():
    made = []

    def create(c):
        if not made:
            made.append(None)
            raise RuntimeError(REFUSAL)
        made.append(c['idempotency_key'])
    return made, create


def test_first_refusal_does_not_abort_later_items_or_duties(monkeypatch):
    reviewed = _sources(monkeypatch)
    made, create = _first_refused()
    out = sw.run(None, Chat, None, now=NOW, create=create, review_create=lambda c: None, dead_chats=True)
    keys = [k for k in made if k]
    # Every item after the refused one was still filed, in every duty.
    assert keys == ['user-steps-stale:req-2', 'user-steps:dup:ns:s1', 'user-steps:dup:ns:r3:r4',
                    'user-steps-failed-decision:op-failed01', 'user-steps-undelivered:op-undeliv1',
                    'user-steps-dead-chat-ask:t_1:20261111_032644_40d5be'], made
    assert len(reviewed) == 1, 'old-ask review must still run'
    errors = [o for o in out if o.startswith('error:')]
    assert errors == [f'error:stale:req-1: {REFUSAL}'[:len('error:stale:req-1: ') + 300]], out
    assert 'req-2' in out and any(o.startswith('old-asks:') for o in out)


def test_a_raising_detector_source_or_review_is_isolated(monkeypatch):
    _sources(monkeypatch)
    monkeypatch.setattr(sw, 'failed_decisions', lambda *a, **k: (_ for _ in ()).throw(OSError('db locked')))
    monkeypatch.setattr(sw, 'review_old_asks', lambda *a, **k: (_ for _ in ()).throw(RuntimeError(REFUSAL)))
    made = []
    out = sw.run(None, Chat, None, now=NOW, create=lambda c: made.append(c['idempotency_key']),
                 review_create=lambda c: None, dead_chats=True)
    assert 'user-steps-undelivered:op-undeliv1' in made and 'user-steps-dead-chat-ask:t_1:20261111_032644_40d5be' in made
    errors = [o for o in out if o.startswith('error:')]
    assert any(e.startswith('error:failed-decision:source: OSError') for e in errors), errors
    assert any(e.startswith('error:old-asks:review: ') and 'brand_new_refusal_shape' in e for e in errors), errors


def test_main_runs_every_duty_then_exits_1_listing_the_errors(monkeypatch, capsys):
    reviewed = _sources(monkeypatch)
    made, create = _first_refused()

    class Cards:
        QualifiedCardDelivery = staticmethod(lambda: None)

    class Core:
        class ActionError(Exception):
            pass

    monkeypatch.setattr(sw, '_load', lambda name: {'core': Core, 'chat': Chat, 'cards': Cards}[name])
    monkeypatch.setattr(sw, '_require_identity', lambda core, argv=None: 'gw')
    monkeypatch.setattr(sw, '_store', lambda core: None)
    monkeypatch.setattr(sw, 'create_card', create)
    monkeypatch.setattr(sw, 'create_review_card', lambda c: 't_review')
    monkeypatch.setattr(sw, 'close_retired_cards', lambda *a, **k: [])
    assert sw.main([]) == 1
    out = capsys.readouterr().out
    assert len([k for k in made if k]) == 6 and len(reviewed) == 1
    doc = json.loads(out.strip().splitlines()[-1])
    assert doc['errors'] == [e for e in doc['cards'] if e.startswith('error:')]
    assert doc['errors'] and doc['errors'][0].startswith('error:stale:req-1: kanban_create failed')

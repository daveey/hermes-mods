"""Desired-invariant regressions for second independent review findings."""
from dataclasses import asdict, replace
import importlib
import pytest
from conftest import PACKAGE, start, Service
from test_native import bridge, native_card, native_project

runtime = importlib.import_module(PACKAGE + '.runtime')
client = importlib.import_module(PACKAGE + '.client')
core = importlib.import_module(PACKAGE + '.service')
InboundChanged = importlib.import_module(PACKAGE + '.native').InboundChanged


def archived(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    return service.closeout(row['workflow_id'], 'done')


@pytest.mark.parametrize('phase', ['backfill', 'receipt'])
def test_archived_transport_failure_preserves_verified_archive(env, phase):
    service, _, ports = env
    row = archived(env)
    def failed(*args):
        raise RuntimeError('Slack thread read failed')
    if phase == 'backfill':
        ports.thread_events = failed
    else:
        ports.read_result = failed
    ports.evidence_ok = False  # A later commit must not invalidate historic completion.
    service.reconcile()
    after = service.store.get(row['workflow_id'])
    assert after['state'] == 'archived' and not after['archive_pending']
    assert ports.get_session(row['stored_session_id'])['archived']
    assert after['error']


def test_archive_polling_is_throttled_bounded_and_live_followups_still_reopen(env, monkeypatch):
    service, ctx, ports = env
    row = archived(env)
    now = row['archived_at']
    calls = []
    ports.thread_events = lambda row: calls.append(row['workflow_id']) or []
    monkeypatch.setattr(core.time, 'time', lambda: now + 1)
    service.reconcile()
    service.reconcile()
    assert len(calls) == 1
    monkeypatch.setattr(core.time, 'time', lambda: now + 302)
    service.reconcile()
    assert len(calls) == 2
    monkeypatch.setattr(core.time, 'time', lambda: now + 86401)
    service.reconcile()
    assert len(calls) == 2
    after = service.followup(replace(ctx, thread_id=ctx.message_id, message_id='later'), 'new work')
    assert after['generation'] == 2 and after['state'] == 'awaiting_approval'
    assert not ports.get_session(row['stored_session_id'])['archived']


@pytest.mark.parametrize('phase', ['backfill', 'receipt'])
def test_positive_invalidation_unarchives_without_rechecking_current_head(env, phase):
    service, _, ports = env
    row = archived(env)
    ports.evidence_ok = False
    if phase == 'backfill':
        def changed(*args):
            raise InboundChanged('source deleted')
        ports.thread_events = changed
    else:
        ports.watermark_ok = False
    service.reconcile()
    after = service.store.get(row['workflow_id'])
    assert after['state'] == 'invalidated'
    assert not ports.get_session(row['stored_session_id'])['archived']
    def no_evidence(*args):
        pytest.fail('Historic invalidation must not verify current HEAD')
    ports.verify_evidence = no_evidence
    service.reconcile()
    assert service.store.get(row['workflow_id'])['state'] == 'invalidated'


def test_invalidated_archive_retries_failed_unarchive(env):
    service, _, ports = env
    row = archived(env)
    ports.watermark_ok = False
    ports.archive_effect = False
    service.reconcile()
    assert service.store.get(row['workflow_id'])['archive_pending']
    assert ports.get_session(row['stored_session_id'])['archived']
    ports.archive_effect = True
    service.reconcile()
    assert not ports.get_session(row['stored_session_id'])['archived']


def test_new_mid_thread_work_rejected_without_native_effects(env):
    service, ctx, ports = env
    with pytest.raises(ValueError, match='root'):
        service.request(replace(ctx, message_id='150.0', thread_id='100.0'), 'work')
    assert service.store.rows() == [] and ports.calls == []


def test_existing_thread_request_is_still_followup(env):
    service, ctx, _ = env
    row = service.request(ctx, 'work')
    after = service.request(replace(ctx, message_id='next', thread_id=ctx.message_id), 'change')
    assert after['workflow_id'] == row['workflow_id'] and after['generation'] == 2


@pytest.mark.parametrize('via_backfill', [True, False])
def test_empty_followup_is_recorded_consistently(env, via_backfill):
    service, ctx, ports = env
    row = service.request(ctx, 'work')
    event = replace(ctx, message_id='next', thread_id=ctx.message_id)
    if via_backfill:
        ports.thread_events = lambda row: [(event, '')]
        service.reconcile()
    else:
        service.followup(event, '')
    after = service.store.get(row['workflow_id'])
    assert after['generation'] == 2
    assert 'Non-text human follow-up' in after['text']
    assert service.store.seen(event)


@pytest.mark.parametrize('kind', ['empty', 'scope', 'mid_thread'])
def test_deterministic_rejections_are_durable_dead_letters(env, tmp_path, kind):
    service, ctx, _ = env
    ctl = runtime.Controller(service)
    class Direct:
        def call(self, op, **kw):
            return ctl.call(op, kw)
    queue = client.DurableIngressQueue(tmp_path / 'queue' / 'q.db', Direct())
    if kind == 'scope':
        ctx = replace(ctx, team_id='wrong')
    if kind == 'mid_thread':
        ctx = replace(ctx, thread_id='elsewhere')
    queue.append('request', context=asdict(ctx), text='' if kind == 'empty' else 'work')
    assert queue.drain_once()
    assert not queue.drain_once()
    with queue.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM rejected').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM pending').fetchone()[0] == 0


def test_empty_followup_journal_drains(env, tmp_path):
    service, ctx, _ = env
    service.request(ctx, 'work')
    ctl = runtime.Controller(service)
    class Direct:
        def call(self, op, **kw):
            return ctl.call(op, kw)
    queue = client.DurableIngressQueue(tmp_path / 'queue' / 'q.db', Direct())
    queue.append('observe_followup', context=asdict(replace(ctx, message_id='next', thread_id=ctx.message_id)), text='')
    assert queue.drain_once() and not queue.drain_once()
    assert service.store.rows()[0]['generation'] == 2


@pytest.mark.parametrize('legacy', [True, False])
@pytest.mark.parametrize('retired', [True, False])
def test_full_service_restart_recovers_uncertain_cards_without_duplicate(env, legacy, retired):
    service, ctx, ports = env
    ports.fail = 'implement'
    row = start(service, ctx, 'work')
    wid = row['workflow_id']
    if legacy:
        row['cards']['implement']['params'].pop('stored_session_id')
        service.store.save(row)
    params = row['cards']['implement']['params']
    stored = ports.get_session(row['stored_session_id'])
    card = native_card(service, row, params, params['idempotency_key'], f'project-work:{wid}:1:implement\n')
    card['status'] = 'archived'
    native = bridge(env, sessions=lambda query: [] if 'runtime_id' in query else [stored],
                    rpc=lambda *args: {'session_id': 'new-live', 'stored_session_id': stored['id']},
                    projects=lambda q: native_project(service), card_keys=lambda q: [card['id']],
                    cards=lambda cid: card)
    before = sum(isinstance(c, tuple) and c[0] == 'create_card' for c in ports.calls)
    # Retain fake unrelated effects, but route restart/uncertain-card recovery
    # through the real bridge with a fresh empty runtime-id map.
    ports.recover_session = native.recover_session
    ports.recover_card = native.recover_card
    ports.fail = None
    fresh = Service(service.policy, service.store, ports)
    if retired:
        after = fresh.followup(replace(ctx, message_id='next', thread_id=ctx.message_id), 'change')
        assert after['history'][0]['retired']
        assert after['state'] == 'awaiting_approval'
        assert sum(isinstance(c, tuple) and c[0] == 'create_card' for c in ports.calls) == before
    else:
        fresh.reconcile()
        after = fresh.store.get(wid)
        assert after['state'] == 'running' and after['session_id'] == 'new-live'
        # Only the never-attempted review/release are new creates.
        assert sum(isinstance(c, tuple) and c[0] == 'create_card' for c in ports.calls) == before + 2
    assert (after['history'][0] if retired else after)['cards']['implement']['id'] == card['id']


def test_publication_gate_is_read_only_and_rejects_new_thread_work(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    cid = row['cards']['release']['id']
    before = service.store.get(row['workflow_id'])
    gate = runtime.Controller(service).call('publication_gate', {'workflow_id': row['workflow_id'], 'card_id': cid})
    assert gate == {'current_generation': 1, 'approved': True, 'card_in_current_generation': True}
    assert service.store.get(row['workflow_id']) == before
    ports.thread_events = lambda r: [(replace(ctx, message_id='late', thread_id=ctx.message_id), 'wait')]
    assert not service.publication_gate(row['workflow_id'], cid)['approved']
    assert not service.publication_gate(row['workflow_id'], 'other')['card_in_current_generation']
    after = service.followup(replace(ctx, message_id='late', thread_id=ctx.message_id), 'wait')
    assert not service.publication_gate(row['workflow_id'], cid)['card_in_current_generation']
    assert after['generation'] == 2


def test_publication_gate_rejects_revoked_approval_and_outage(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    cid = row['cards']['release']['id']
    ports.verify_approval = lambda *args: False
    assert not service.publication_gate(row['workflow_id'], cid)['approved']
    def outage(*args):
        raise TimeoutError('unavailable')
    ports.verify_approval = outage
    with pytest.raises(TimeoutError):
        service.publication_gate(row['workflow_id'], cid)


def test_out_of_order_backfill_can_close_and_rejects_unseen_older_message(env):
    service, ctx, ports = env
    root = replace(ctx, message_id='1.0')
    row = service.request(root, 'work')
    latest = replace(root, message_id='3.0', thread_id='1.0')
    delayed = replace(root, message_id='2.0', thread_id='1.0')
    service.followup(latest, 'latest')
    ports.thread_events = lambda row: [(root, 'work'), (delayed, 'delayed'), (latest, 'latest')]
    service.reconcile()
    row = service.store.get(row['workflow_id'])
    assert row['message_id'] == '3.0' and row['generation'] == 3
    row = service.propose_plan(row['workflow_id'], 'incorporate everything', 3)
    row = service.approve(replace(root, message_id='4.0', thread_id='1.0'), row['workflow_id'], row['plan_hash'])
    messages = [dict(ts='1.0', user='owner', text='work'),
                dict(ts='2.0', thread_ts='1.0', user='owner', text='delayed'),
                dict(ts='3.0', thread_ts='1.0', user='owner', text='latest'),
                dict(ts='4.0', thread_ts='1.0', user='owner',
                     text=f"!project approve {row['workflow_id']} {row['plan_hash']}"),
                dict(ts='5.0', thread_ts='1.0', user='bot', text='done')]
    native = bridge(env, slack=lambda *args: dict(ok=True, messages=messages))
    ports.read_result = native.read_result
    ports.post_result = lambda *args: dict(channel='channel', ts='5.0')
    ports.done()
    after = service.closeout(row['workflow_id'], 'done')
    assert after['state'] == 'archived'
    messages.append(dict(ts='2.5', thread_ts='1.0', user='owner', text='missed obligation'))
    assert not native.read_result(after, after['result_receipt'], 'done')

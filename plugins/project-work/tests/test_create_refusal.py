"""Typed native card-create refusal and explicit create_intent operator recovery."""
import copy
import importlib
import json
import pytest
from dataclasses import replace
from conftest import PACKAGE, core, start
from test_native import bridge, native_card, native_project

errors = importlib.import_module(PACKAGE + '.errors')
WorkflowError = core.WorkflowError

CODE = 'assignee_capability_mismatch'
MESSAGE = "Assignee 'builder' lacks required capabilities: host_repo"


def refused_class():
    # Looked up per test so a missing class fails each test, not collection.
    return errors.NativeCreateRefused


# ---------------------------------------------------------------- native bridge

def native_setup(env, result, keys):
    """A NativeBridge whose kanban_create returns `result` and whose
    archived-inclusive card_keys lookups return successive entries of `keys`."""
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    params = row['cards']['implement']['params']
    marker = f"project-work:{row['workflow_id']}:{row['generation']}:implement\n"
    creates, queries = [], []
    keys = list(keys)

    def owner_tool(name, args, sid):
        creates.append((name, args, sid))
        return result

    def card_keys(query):
        queries.append(query)
        answer = keys.pop(0) if keys else []
        if isinstance(answer, Exception):
            raise answer
        return answer

    native = bridge(env, owner_tool=owner_tool, card_keys=card_keys,
                    cards=lambda cid: native_card(service, row, params, cid, marker),
                    projects=lambda q: native_project(service),
                    sessions=lambda q: [ports.get_session(row['stored_session_id'])])
    native.live[row['session_id']] = row['stored_session_id']
    return native, row, params, creates, queries


@pytest.mark.parametrize('as_json', [False, True])
def test_structured_refusal_with_empty_lookup_is_typed(env, as_json):
    result = {'error': MESSAGE, 'code': CODE}
    native, row, params, creates, queries = native_setup(
        env, json.dumps(result) if as_json else result, [[], []])
    with pytest.raises(refused_class()) as caught:
        native.create_card(row['workflow_id'], 'implement', params)
    assert caught.value.code == CODE
    assert caught.value.error == MESSAGE
    assert caught.value.idempotency_key == params['idempotency_key']
    assert isinstance(caught.value, RuntimeError)
    assert len(creates) == 1
    # Pre-create lookup plus the fresh post-refusal lookup, both archived-inclusive.
    assert len(queries) == 2
    assert all(q['include_archived'] is True and q['idempotency_key'] == params['idempotency_key']
               for q in queries)


def test_refusal_whose_post_lookup_finds_card_returns_validated_card(env):
    native, row, params, creates, queries = native_setup(
        env, {'error': MESSAGE, 'code': CODE}, [[], ['native-task']])
    assert native.create_card(row['workflow_id'], 'implement', params) == 'native-task'
    assert len(creates) == 1 and len(queries) == 2


@pytest.mark.parametrize('result', [
    {'error': MESSAGE},                       # no code
    {'error': MESSAGE, 'code': ''},           # empty code
    {'error': MESSAGE, 'code': 7},            # non-string code
    {'code': CODE},                           # no error field
    ['not', 'a', 'dict'],                     # non-dict
    json.dumps(['not', 'a', 'dict']),         # non-dict JSON
    None,                                     # non-dict
])
def test_unstructured_result_stays_ambiguous(env, result):
    native, row, params, _, _ = native_setup(env, result, [[], []])
    with pytest.raises(RuntimeError) as caught:
        native.create_card(row['workflow_id'], 'implement', params)
    assert not isinstance(caught.value, refused_class())


@pytest.mark.parametrize('lookup', [TimeoutError('reader down'), ['a', 'b'], ['wrong-scope']])
def test_refusal_with_failing_post_lookup_stays_ambiguous(env, lookup):
    service, ctx, ports = env
    native, row, params, _, _ = native_setup(env, {'error': MESSAGE, 'code': CODE}, [[], lookup])
    if lookup == ['wrong-scope']:
        # A found card that fails exact scope validation is not proof of absence.
        native.cards = lambda cid: dict(native_card(service, row, params, cid, 'other\n'))
    with pytest.raises(RuntimeError) as caught:
        native.create_card(row['workflow_id'], 'implement', params)
    assert type(caught.value) is RuntimeError
    assert not isinstance(caught.value, refused_class())


# -------------------------------------------------------------- service helpers

def refuse(ports, stage, codes):
    """Wrap the fake create_card: refuse `stage` with each code in turn, recording params."""
    original = ports.create_card
    ports.create_params = []
    pending = list(codes)

    def create_card(workflow_id, card_stage, params):
        ports.create_params.append((card_stage, copy.deepcopy(params)))
        if card_stage == stage and pending:
            ports.calls.append(('create_card', params['idempotency_key']))
            raise refused_class()(pending.pop(0), MESSAGE, params['idempotency_key'])
        return original(workflow_id, card_stage, params)
    ports.create_card = create_card
    return pending


def creates(ports):
    return [c for c in ports.calls if isinstance(c, tuple) and c[0] == 'create_card']


def capture_raises(service):
    raised = []
    original = service._attempt

    def attempt(row, operation):
        def wrapped(r):
            try:
                operation(r)
            except Exception as exc:
                raised.append(exc)
                raise
        return original(row, wrapped)
    service._attempt = attempt
    return raised


def raw(service, workflow_id):
    with service.store.connect() as db:
        return db.execute('SELECT data FROM workflows WHERE workflow_id=?', (workflow_id,)).fetchone()[0]


# ------------------------------------------------------------- service: stage loop

def test_first_attempt_refusal_persists_create_refused(env):
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE])
    raised = capture_raises(service)
    row = start(service, ctx, 'work')
    entry = row['cards']['implement']
    assert entry['state'] == 'create_refused' and 'id' not in entry
    assert entry['params'] == ports.create_params[0][1]
    refusal = entry['refusal']
    assert refusal['code'] == CODE and refusal['error'] == MESSAGE
    assert refusal['idempotency_key'] == entry['params']['idempotency_key']
    assert isinstance(refusal['at'], (int, float))
    assert 'refusals' not in entry
    assert row['error'] == 'card_create_refused:' + CODE
    assert set(row['cards']) == {'implement'}  # later stages not attempted
    assert [type(e) for e in raised] and isinstance(raised[-1], WorkflowError)
    assert raised[-1].code == CODE


def test_retry_from_create_refused_reuses_params_and_creates(env):
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE])
    row = start(service, ctx, 'work')
    params = copy.deepcopy(row['cards']['implement']['params'])
    first = copy.deepcopy(row['cards']['implement']['refusal'])
    row = service.reconcile()[0]
    entry = row['cards']['implement']
    assert entry['state'] == 'created' and entry['id'] == params['idempotency_key']
    assert entry['params'] == params
    assert entry['refusal'] == first  # history kept
    implement_creates = [p for s, p in ports.create_params if s == 'implement']
    assert implement_creates == [params, params]
    assert row['state'] == 'running' and row['error'] is None


def test_retry_from_create_refused_adopts_recovered_card_without_create(env):
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE])
    row = start(service, ctx, 'work')
    assert row['cards']['implement']['state'] == 'create_refused'
    first = copy.deepcopy(row['cards']['implement']['refusal'])
    params = row['cards']['implement']['params']
    key = params['idempotency_key']
    # Operator fixed the cause and the card now exists natively (even archived).
    ports.cards[key] = dict(id=key, status='archived', **params)
    recovered = []
    original = ports.recover_card
    ports.recover_card = lambda w, s, p: recovered.append((s, p)) or original(w, s, p)
    before = len([s for s, _ in ports.create_params if s == 'implement'])
    row = service.reconcile()[0]
    entry = row['cards']['implement']
    assert entry['state'] == 'created' and entry['id'] == key
    assert recovered[0] == ('implement', params)
    assert len([s for s, _ in ports.create_params if s == 'implement']) == before
    assert entry['params'] == params and entry['refusal'] == first


def test_repeated_refusal_appends_history_with_same_key(env):
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE, 'second_code'])
    raised = capture_raises(service)
    row = start(service, ctx, 'work')
    params = copy.deepcopy(row['cards']['implement']['params'])
    first = copy.deepcopy(row['cards']['implement']['refusal'])
    row = service.reconcile()[0]
    entry = row['cards']['implement']
    assert entry['state'] == 'create_refused' and 'id' not in entry
    assert entry['params'] == params
    assert entry['refusal']['code'] == 'second_code'
    assert entry['refusal']['idempotency_key'] == params['idempotency_key']
    assert entry['refusals'] == [first]
    assert row['error'] == 'card_create_refused:second_code'
    assert raised[-1].code == 'second_code'
    assert [p for s, p in ports.create_params if s == 'implement'] == [params, params]


def test_ambiguous_retry_from_create_refused_reenters_operator_path(env):
    # The retry re-persists create_intent before its effect, so an ambiguous
    # outcome is never blindly retried; recover_card_intent resolves it.
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE])
    row = start(service, ctx, 'work')
    first = copy.deepcopy(row['cards']['implement']['refusal'])
    key = row['cards']['implement']['params']['idempotency_key']
    ports.fail = 'implement'
    row = service.reconcile()[0]
    ports.cards.pop(key)  # The uncertain retry did not apply.
    ports.fail = None
    entry = row['cards']['implement']
    assert entry['state'] == 'create_intent' and entry['refusal'] == first
    assert row['error'] == 'TimeoutError'
    count = len(creates(ports))
    row = service.reconcile()[0]
    assert row['error'] == 'WorkflowError: Ambiguous card creation; operator recovery required'
    assert len(creates(ports)) == count
    entry = service.recover_card_intent(row['workflow_id'], 'implement')
    assert entry['state'] == 'created' and entry['refusal'] == first
    assert entry['recovered_from'] == 'create_intent'


def test_legacy_create_intent_normal_path_stays_ambiguous(env):
    service, ctx, ports = env
    ports.fail = 'implement'
    row = start(service, ctx, 'work')
    key = row['cards']['implement']['params']['idempotency_key']
    ports.cards.pop(key)  # The uncertain create did not apply.
    ports.fail = None
    row = service.reconcile()[0]
    assert row['cards']['implement']['state'] == 'create_intent'
    assert row['error'] == 'WorkflowError: Ambiguous card creation; operator recovery required'
    assert len(creates(ports)) == 1


# ------------------------------------------------------- service: recover_card_intent

def legacy_intent(env, stage='implement'):
    service, ctx, ports = env
    ports.fail = stage
    row = start(service, ctx, 'work')
    ports.fail = None
    assert row['cards'][stage]['state'] == 'create_intent'
    return service, ports, row


def test_recover_card_intent_adopts_archived_card_without_create(env):
    service, ports, row = legacy_intent(env)
    params = row['cards']['implement']['params']
    ports.cards[params['idempotency_key']]['status'] = 'archived'
    count = len(creates(ports))
    entry = service.recover_card_intent(row['workflow_id'], 'implement')
    assert entry['state'] == 'created' and entry['id'] == params['idempotency_key']
    assert entry['recovered_from'] == 'create_intent'
    assert entry['params'] == params
    assert len(creates(ports)) == count
    stored = service.store.get(row['workflow_id'])
    assert stored['cards']['implement'] == entry
    assert set(stored['cards']) == {'implement'}  # no other stage advanced


def test_recover_card_intent_miss_creates_once(env):
    service, ports, row = legacy_intent(env)
    params = row['cards']['implement']['params']
    ports.cards.pop(params['idempotency_key'])
    refuse(ports, 'implement', [])
    count = len(creates(ports))
    prompts = [c for c in ports.calls if isinstance(c, tuple) and c[0] == 'prompt']
    entry = service.recover_card_intent(row['workflow_id'], 'implement')
    assert entry['state'] == 'created' and entry['id'] == params['idempotency_key']
    assert entry['recovered_from'] == 'create_intent'
    assert len(creates(ports)) == count + 1
    assert ports.create_params == [('implement', params)]
    assert [c for c in ports.calls if isinstance(c, tuple) and c[0] == 'prompt'] == prompts
    assert set(service.store.get(row['workflow_id'])['cards']) == {'implement'}


def test_recover_card_intent_refusal_records_create_refused(env):
    service, ports, row = legacy_intent(env)
    params = row['cards']['implement']['params']
    ports.cards.pop(params['idempotency_key'])
    refuse(ports, 'implement', [CODE])
    entry = service.recover_card_intent(row['workflow_id'], 'implement')
    assert entry['state'] == 'create_refused' and 'id' not in entry
    assert entry['recovered_from'] == 'create_intent'
    assert entry['params'] == params
    assert entry['refusal']['code'] == CODE
    assert entry['refusal']['idempotency_key'] == params['idempotency_key']
    stored = service.store.get(row['workflow_id'])
    assert stored['cards']['implement'] == entry
    assert stored['error'] == 'card_create_refused:' + CODE


def test_recover_card_intent_ambiguous_leaves_entry_byte_identical(env):
    service, ports, row = legacy_intent(env)
    params = row['cards']['implement']['params']
    ports.cards.pop(params['idempotency_key'])
    before = raw(service, row['workflow_id'])
    ports.fail = 'implement'
    with pytest.raises(TimeoutError):
        service.recover_card_intent(row['workflow_id'], 'implement')
    assert raw(service, row['workflow_id']) == before


def _mutate(service, workflow_id, change):
    row = service.store.get(workflow_id)
    change(row)
    service.store.save(row)


@pytest.mark.parametrize('case', ['unapproved', 'created', 'existing_id', 'refused_state',
                                  'missing', 'stale_generation', 'unknown_stage', 'predecessor'])
def test_recover_card_intent_refuses_without_writing(env, case):
    service, ports, row = legacy_intent(env, 'review' if case == 'predecessor' else 'implement')
    wid, stage = row['workflow_id'], 'implement'
    if case == 'unapproved':
        _mutate(service, wid, lambda r: r.update(approved=False))
    elif case == 'created':
        _mutate(service, wid, lambda r: r['cards']['implement'].update(state='created'))
    elif case == 'existing_id':
        _mutate(service, wid, lambda r: r['cards']['implement'].update(id='task'))
    elif case == 'refused_state':
        _mutate(service, wid, lambda r: r['cards']['implement'].update(state='create_refused'))
    elif case == 'missing':
        stage = 'release'
    elif case == 'stale_generation':
        _mutate(service, wid, lambda r: r['cards']['implement']['params'].update(generation=0))
    elif case == 'unknown_stage':
        stage = 'deploy'
    elif case == 'predecessor':
        stage = 'review'
        _mutate(service, wid, lambda r: r['cards']['implement'].pop('id'))
    before, calls = raw(service, wid), list(ports.calls)
    recovered = []
    ports.recover_card = lambda *a: recovered.append(a)
    with pytest.raises(WorkflowError):
        service.recover_card_intent(wid, stage)
    assert raw(service, wid) == before
    assert ports.calls == calls and recovered == []


def test_recover_card_intent_review_stage_after_created_implement(env):
    service, ports, row = legacy_intent(env, 'review')
    params = row['cards']['review']['params']
    assert params['depends_on'] == [row['cards']['implement']['id']]
    ports.cards.pop(params['idempotency_key'])
    entry = service.recover_card_intent(row['workflow_id'], 'review')
    assert entry['state'] == 'created' and entry['recovered_from'] == 'create_intent'
    assert set(service.store.get(row['workflow_id'])['cards']) == {'implement', 'review'}


# ------------------------------------------- bounded history and retirement

def test_refusal_history_is_bounded(env):
    service, ctx, ports = env
    limit = core.REFUSAL_HISTORY
    refuse(ports, 'implement', [f'code{i}' for i in range(limit + 5)])
    row = start(service, ctx, 'work')
    for _ in range(limit + 4):
        row = service.reconcile()[0]
    entry = row['cards']['implement']
    assert entry['refusal']['code'] == f'code{limit + 4}'
    assert len(entry['refusals']) == limit
    assert entry['refusals'][-1]['code'] == f'code{limit + 3}'
    assert entry['refusals'][0]['code'] == 'code4'


def test_retired_generation_create_refused_is_absent_not_ambiguous(env):
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE])
    row = start(service, ctx, 'work')
    assert row['cards']['implement']['state'] == 'create_refused'
    old_key = row['cards']['implement']['params']['idempotency_key']
    quiesced = []
    ports.quiesce_generation = lambda r, old: quiesced.append(old['generation']) or True
    followup = replace(ctx, message_id='next', thread_id=ctx.message_id)
    row = start(service, followup, 'more work')
    assert row['generation'] == 2 and quiesced == [1]
    old = row['history'][0]
    assert old['retired'] is True
    assert old['cards']['implement']['state'] == 'refused_retired'
    assert 'id' not in old['cards']['implement']
    assert old['cards']['implement']['params']['idempotency_key'] == old_key
    assert row['cards']['implement']['state'] == 'created'


def test_retired_generation_refused_retired_reentry_is_absent(env):
    """A crash after saving refused_retired but before retired=True re-enters as absent, not ambiguous."""
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE])
    row = start(service, ctx, 'work')
    assert row['cards']['implement']['state'] == 'create_refused'
    row['history'] = [dict(generation=row['generation'], cards=copy.deepcopy(row['cards']))]
    row['history'][0]['cards']['implement']['state'] = 'refused_retired'
    service.store.save(row)
    ports.quiesce_generation = lambda r, old: True
    service._retire(service.store.get(row['workflow_id']))
    old = service.store.get(row['workflow_id'])['history'][0]
    assert old['retired'] is True
    assert old['cards']['implement']['state'] == 'refused_retired'
    assert 'id' not in old['cards']['implement']


def test_retired_generation_plain_create_intent_stays_ambiguous(env):
    service, ctx, ports = env
    original = ports.create_card

    def lost(workflow_id, stage, params):
        if stage == 'implement':
            raise RuntimeError('transport lost')
        return original(workflow_id, stage, params)
    ports.create_card = lost
    raised = capture_raises(service)
    row = start(service, ctx, 'work')
    assert row['cards']['implement']['state'] == 'create_intent'
    ports.create_card = original
    ports.quiesce_generation = lambda r, old: True
    followup = replace(ctx, message_id='next', thread_id=ctx.message_id)
    row = start(service, followup, 'more work')
    assert 'retired' not in row['history'][0] or row['history'][0]['retired'] is not True
    assert any('Earlier ambiguous card needs operator recovery' in str(e) for e in raised)


# ------------------------------------------- run27 C-M2: invariants observed from the port

def store_lock_state(service):
    """(process-local lock held?, cross-process flock held?) for the service's ledger."""
    import fcntl
    store_mod = importlib.import_module(PACKAGE + '.store')
    local = store_mod._LOCKS.get(str(service.store.path))
    with open(str(service.store.path) + '.lock', 'a') as probe:  # new open file description
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            flocked = True
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            flocked = False
    return bool(local is not None and local.locked()), flocked


def test_retry_from_create_refused_persists_create_intent_before_create(env):
    # The integration owner_tool re-reads the ledger and refuses unless the STORED
    # state is create_intent, so the save must precede the effect (not only _attempt's).
    service, ctx, ports = env
    refuse(ports, 'implement', [CODE])
    row = start(service, ctx, 'work')
    workflow_id = row['workflow_id']
    params = copy.deepcopy(row['cards']['implement']['params'])
    first = copy.deepcopy(row['cards']['implement']['refusal'])
    seen = []
    original = ports.create_card

    def create_card(wid, stage, p):
        if stage == 'implement':
            seen.append(copy.deepcopy(service.store.get(wid)['cards']['implement']))
        return original(wid, stage, p)
    ports.create_card = create_card
    row = service.reconcile()[0]
    assert row['cards']['implement']['state'] == 'created'
    assert len(seen) == 1
    assert seen[0]['state'] == 'create_intent' and 'id' not in seen[0]
    assert seen[0]['params'] == params and seen[0]['refusal'] == first
    assert service.store.get(workflow_id)['cards']['implement']['state'] == 'created'


@pytest.mark.parametrize('outcome', ['created', 'refused'])
def test_recover_card_intent_holds_store_lock_across_recover_and_create(env, outcome):
    service, ports, row = legacy_intent(env)
    params = row['cards']['implement']['params']
    ports.cards.pop(params['idempotency_key'])
    assert store_lock_state(service) == (False, False)  # nothing else holds it now
    refuse(ports, 'implement', [CODE] if outcome == 'refused' else [])
    observed = []
    original_create, original_recover = ports.create_card, ports.recover_card

    def recover_card(w, s, p):
        observed.append(('recover_card', store_lock_state(service)))
        return original_recover(w, s, p)

    def create_card(w, s, p):
        observed.append(('create_card', store_lock_state(service)))
        return original_create(w, s, p)
    ports.recover_card, ports.create_card = recover_card, create_card
    entry = service.recover_card_intent(row['workflow_id'], 'implement')
    assert entry['state'] == ('created' if outcome == 'created' else 'create_refused')
    assert observed == [('recover_card', (True, True)), ('create_card', (True, True))]
    assert store_lock_state(service) == (False, False)  # released afterwards

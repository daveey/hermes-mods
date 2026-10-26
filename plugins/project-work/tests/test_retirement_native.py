import pytest
from conftest import start
from test_native import bridge


def test_retirement_requires_exact_proof_even_unknown_card_id(env):
    service, ctx, _ = env
    row = start(service, ctx, 'work')
    old = dict(row)
    old['cards'] = {'implement': dict(row['cards']['implement'])}
    old['cards']['implement'].pop('id')
    native = bridge(env)
    with pytest.raises(RuntimeError, match='retirement adapter'):
        native.quiesce_generation(row, old)
    proof = dict(workflow_id=row['workflow_id'], generation=row['generation'],
                 publication_revoked=True, live_writer=False, pending_dispatch=False,
                 idempotency_keys=[old['cards']['implement']['params']['idempotency_key']])
    native.quiesce = lambda *args: proof
    assert native.quiesce_generation(row, old)
    for key, value in [('publication_revoked', False), ('live_writer', True),
                       ('pending_dispatch', True), ('generation', 999), ('idempotency_keys', [])]:
        native.quiesce = lambda *args, key=key, value=value: dict(proof, **{key: value})
        assert not native.quiesce_generation(row, old)


def test_deleted_earlier_source_is_not_silently_ignored(env):
    native = bridge(env, slack=lambda *a: dict(ok=True, messages=[
        dict(ts='2.0', thread_ts='1.0', user='owner', text='latest')]))
    row = dict(channel_id='channel', thread_id='1.0', message_id='2.0', source_ids=['1.0', '2.0'])
    with pytest.raises(RuntimeError, match='deleted'):
        native.thread_events(row)

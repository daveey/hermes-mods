"""Desired-invariant regressions for pinned PR10 e3aec4a; reviewer-only."""
from dataclasses import replace
from conftest import start
from test_native import bridge, native_project, native_card


def test_uncertain_card_recovery_after_gateway_runtime_id_changes(env):
    service, ctx, ports = env
    ports.fail = 'implement'
    row = start(service, ctx, 'work')
    params = row['cards']['implement']['params']
    original_live = row['session_id']
    stored = ports.get_session(row['stored_session_id'])
    card = native_card(service, row, params, 'native-task',
                       f"project-work:{row['workflow_id']}:1:implement\n")
    card['status'] = 'archived'
    creates = []
    def sessions(query):
        if 'runtime_id' in query:
            return [stored] if query['runtime_id'] == 'new-live' else []
        return [stored]
    native = bridge(env, sessions=sessions,
        rpc=lambda *a: {'session_id': 'new-live', 'stored_session_id': stored['id']},
        projects=lambda q: native_project(service), card_keys=lambda q: ['native-task'],
        cards=lambda cid: card, owner_tool=lambda *a: creates.append(a))
    # Fresh bridge and gateway: the old live ID is gone. The normal _advance
    # recovery already established the exact same durable session under a new ID.
    recovered = native.recover_session(row['workflow_id'])
    assert recovered['session_id'] != original_live
    assert recovered['stored_session_id'] == row['stored_session_id']
    assert native.recover_card(row['workflow_id'], 'implement', params) == 'native-task'
    assert creates == []


def test_processed_out_of_order_followups_do_not_wedge_watermark(env):
    service, ctx, ports = env
    ctx = replace(ctx, message_id='1.0')
    row = start(service, ctx, 'original')
    service.followup(replace(ctx, message_id='3.0', thread_id='1.0'), 'later event delivered first')
    row = service.followup(replace(ctx, message_id='2.0', thread_id='1.0'), 'earlier event delivered late')
    row = service.propose_plan(row['workflow_id'], 'handle both followups', row['generation'])
    row = service.approve(replace(ctx, message_id='4.0', thread_id='1.0'), row['workflow_id'], row['plan_hash'])
    assert set(row['source_ids']) == {'1.0', '2.0', '3.0'}
    messages = [dict(ts='1.0', user='owner', text='original'),
        dict(ts='2.0', thread_ts='1.0', user='owner', text='earlier event delivered late'),
        dict(ts='3.0', thread_ts='1.0', user='owner', text='later event delivered first'),
        dict(ts='4.0', thread_ts='1.0', user='owner',
             text=f"!project approve {row['workflow_id']} {row['plan_hash']}"),
        dict(ts='5.0', thread_ts='1.0', user='bot', text='done')]
    native = bridge(env, slack=lambda *a: dict(ok=True, messages=messages))
    # Every work event is already incorporated into this approved generation.
    assert native.read_result(row, dict(channel='channel', ts='5.0'), 'done')

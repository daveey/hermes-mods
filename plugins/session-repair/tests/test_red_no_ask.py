"""Synthetic red-card shapes; detector never changes lifecycle or permission holds."""
import json
import sqlite3
import uuid

import pytest
pytest.importorskip('hermes_cli.kanban_db')
from sr_helpers import Env, fixtures
F = fixtures()


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    monkeypatch.setattr(e.scan_mod, '_red_chat_live', lambda *_: False)
    e.state.execute("INSERT INTO sessions(id,title,outcome_json) VALUES (?,?,?)",
                    (F.CHAT, 'Synthetic chat', json.dumps({'status': {'presentation': {'stripe': 'stuck'}}})))
    e.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',
                    (F.CHAT, 'assistant', 'Stopped with work retained.', e.now))
    e.state.commit()
    return e


def hits(e):
    return e.scan_mod.red_no_ask_findings(e.sources(), only=F.CHAT)


def card(e, kind: str | None = 'execution_refusal', status='triage'):
    tid = F.card(e.conn, 'Synthetic held work', status=status, block_kind=kind)
    F.event(e.conn, tid, 'blocked', {'status': status, 'reason': 'Preserve this stop'}, at=e.now)
    return tid


@pytest.mark.parametrize('kind', ['spend_budget', 'scope', 'execution_refusal', 'containment', 'lane_gate', 'needs_input'])
def test_retained_hold_shapes(env, kind):
    tid = card(env, kind)
    before = dict(env.conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone())
    f, = hits(env)
    assert f.fix['kind'] == 'nudge' and f.area == 'card' and f.card is None
    assert f.evidence['red_cards'] == [{'card': tid, 'board': 'default', 'status': 'triage',
                                        'hold_kind': kind, 'reason': 'Preserve this stop', 'execution_refusal': False}]
    assert f.fingerprint == f'BS-red-no-ask:chat/{F.CHAT}:{tid}'
    assert dict(env.conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()) == before


def test_each_card_needs_coverage_and_fingerprint_is_sorted(env):
    a, b = card(env), card(env)
    f, = hits(env)
    assert f.fingerprint.endswith(':{}'.format(','.join(sorted([a, b]))))
    env.conn.execute('UPDATE tasks SET human_request=?,block_kind=? WHERE id=?',
                     (json.dumps(F.VALID_ASK), 'needs_input', a))
    env.conn.commit()
    f, = hits(env)
    assert [r['card'] for r in f.evidence['red_cards']] == [b]
    env.conn.execute('UPDATE tasks SET human_request=?,block_kind=? WHERE id=?',
                     (json.dumps(F.VALID_ASK), 'needs_input', b))
    env.conn.commit()
    assert hits(env) == []


def put_ask(env, tid, state='open'):
    root = env.home / 'user-steps'
    root.mkdir(exist_ok=True)
    c = sqlite3.connect(root / 'actions.sqlite3')
    c.executescript('CREATE TABLE IF NOT EXISTS requests(namespace,id,session,revision,state,resolution);'
                    'CREATE TABLE IF NOT EXISTS revisions(namespace,id,revision,body);')
    rid = str(uuid.uuid4())
    ref = {'gatewayId': 'install:example', 'profile': 'default', 'type': 'kanban', 'board': 'default', 'id': tid}
    body = {'schemaVersion': 2, 'owner': {'gatewayId': 'install:example', 'profile': 'default', 'storedSessionId': F.CHAT},
            'requestId': rid, 'revision': 1, 'kind': 'choice', 'title': 'Choose scope',
            'scope': {'markdown': 'Decide scope for the explicitly named card.', 'complete': state == 'open', 'sourceRefs': [ref]},
            'limits': [], 'exclusions': [], 'outcomes': {'answer': 'Record the chosen scope'},
            'choices': [{'id': 'keep', 'label': 'Keep stopped', 'effect': 'answer'}], 'aliases': [], 'state': state}
    c.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL)', ('example', rid, F.CHAT, 1, state))
    c.execute('INSERT INTO revisions VALUES (?,?,?,?)', ('example', rid, 1, json.dumps(body)))
    c.commit()
    c.close()


def test_unrelated_and_incomplete_user_steps_do_not_suppress(env):
    tid = card(env)
    put_ask(env, 't_unrelated')
    assert hits(env)
    put_ask(env, tid, 'needs_context')
    assert hits(env)
    put_ask(env, tid)
    assert hits(env) == []


def test_new_ask_and_new_messages_change_revalidation(env):
    tid = card(env)
    old, = hits(env)
    put_ask(env, 't_unrelated')
    current, = hits(env)
    assert current.evidence['ask_signature'] != old.evidence['ask_signature']
    env.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',
                      (F.CHAT, 'user', 'Keep the hold.', env.now + 1))
    env.state.commit()
    assert hits(env)[0].evidence['last_message_id'] != old.evidence['last_message_id']
    put_ask(env, tid)
    assert hits(env) == []


@pytest.mark.parametrize('column,value', [('muted', 1), ('archived', 1), ('source', 'cron'),
                                         ('end_reason', 'moved_to_gateway:elsewhere')])
def test_nonhuman_or_hidden_sessions_excluded(env, column, value):
    card(env)
    env.state.execute(f'UPDATE sessions SET {column}=? WHERE id=?', (value, F.CHAT))
    env.state.commit()
    assert hits(env) == []


def test_live_foreign_repair_and_ours_excluded(env, monkeypatch):
    tid = card(env)
    monkeypatch.setattr(env.scan_mod, '_red_chat_live', lambda *_: True)
    assert not hits(env)
    monkeypatch.setattr(env.scan_mod, '_red_chat_live', lambda *_: False)
    monkeypatch.setattr(env.scan_mod.Sources, 'copies', lambda *_: {tid})
    assert not hits(env)
    monkeypatch.setattr(env.scan_mod.Sources, 'copies', lambda *_: set())
    monkeypatch.setattr(env.scan_mod.Sources, 'ours_ids', lambda *_: {tid})
    assert not hits(env)
    monkeypatch.setattr(env.scan_mod.Sources, 'ours_ids', lambda *_: set())
    src = env.sources()
    src.exclude_chats.add(F.CHAT)
    assert not env.scan_mod.red_no_ask_findings(src)


def test_invalid_ask_does_not_suppress(env):
    tid = card(env, 'needs_input')
    env.conn.execute('UPDATE tasks SET human_request=? WHERE id=?', ('{"action":"vague"}', tid))
    env.conn.commit()
    assert hits(env)


def test_unreadable_store_and_outcome_fail_closed(env):
    card(env)
    root = env.home / 'user-steps'
    root.mkdir()
    (root / 'actions.sqlite3').write_text('not sqlite')
    assert not hits(env)
    (root / 'actions.sqlite3').unlink()
    env.state.execute('UPDATE sessions SET outcome_json=? WHERE id=?', ('bad json', F.CHAT))
    env.state.commit()
    assert not hits(env)


def test_missing_outcome_is_not_an_error_and_core_overdue_still_applies(env):
    tid = card(env)
    env.state.execute('UPDATE sessions SET outcome_json=NULL WHERE id=?', (F.CHAT,))
    env.state.commit()
    F.event(env.conn, tid, 'decision_retained', {}, at=env.now-900)
    F.age_all_events(env.conn, tid, env.now-900)
    result = env.scan()
    assert result['error_count'] == 0, result['errors']
    assert [f for f in result['findings'] if f['id'] == 'BS-red-no-ask']


def test_overdue_card_even_when_other_ask_makes_chat_yellow(env):
    tid = card(env)
    env.state.execute('UPDATE sessions SET outcome_json=? WHERE id=?',
                      (json.dumps({'status': {'presentation': {'stripe': 'blocked'}}}), F.CHAT))
    env.state.commit()
    assert not hits(env)
    F.event(env.conn, tid, 'decision_retained', {}, at=env.now-900)
    F.age_all_events(env.conn, tid, env.now-900)
    assert hits(env)


def test_owner_pseudo_session_resolves_but_is_never_a_target(env, monkeypatch):
    tid = card(env)
    monkeypatch.setattr(env.scan_mod.Sources, 'linked_chats',
                        lambda *_: {'kanban_owner_synthetic': {('default', tid)}})
    monkeypatch.setattr(env.scan_mod, '_red_creator_chats', lambda *_: {F.CHAT})
    assert hits(env)[0].chat == F.CHAT
    monkeypatch.setattr(env.scan_mod, '_red_creator_chats', lambda *_: set())
    assert not hits(env)


def test_creator_resolver_filters_foreign_profiles_and_machine_fallback(env, monkeypatch):
    from hermes_cli import kanban_creator_chats, profiles
    tid = card(env)
    seen = []
    def resolve(conn, cid):
        seen.append(cid)
        return [{'chat_gateway': gateway, 'chat_profile': profile, 'chat_session': sid}
                for gateway, profile, sid in [('local', 'default', F.CHAT),
                    ('remote', 'default', 'foreign'), ('local', 'other', 'other-profile'),
                    ('local', 'default', 'kanban_owner_synthetic')]]
    monkeypatch.setattr(kanban_creator_chats, 'resolve_chats', resolve)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    src = env.sources()
    monkeypatch.setattr(src, 'own_gateway', lambda _: 'local')
    assert env.scan_mod._red_creator_chats(src, 'default', tid) == {F.CHAT}
    assert seen == [tid]


def test_unreadable_live_owner_fails_closed(env, monkeypatch):
    card(env)
    def unavailable(*_):
        raise RuntimeError('live-owner source unavailable')
    monkeypatch.setattr(env.scan_mod, '_red_chat_live', unavailable)
    src = env.sources()
    assert not env.scan_mod.red_no_ask_findings(src)
    assert any('live-owner source unavailable' in e['error'] for e in src.errors)


def test_scan_integration(env):
    card(env)
    result = env.scan(rules=['BS-red-no-ask'])
    assert [f['id'] for f in result['findings']] == ['BS-red-no-ask']
    assert result['red_no_ask'] == {'chats': 1, 'red_cards': 1, 'covered': 0, 'uncovered': 1}


def test_covered_chat_counts_prove_zero_is_not_an_empty_scan(env):
    tid = card(env)
    env.conn.execute('UPDATE tasks SET human_request=?,block_kind=? WHERE id=?',
                     (json.dumps(F.VALID_ASK), 'needs_input', tid))
    env.conn.commit()
    result = env.scan(rules=['BS-red-no-ask'])
    assert result['findings'] == []
    assert result['red_no_ask'] == {'chats': 1, 'red_cards': 1, 'covered': 1, 'uncovered': 0}


def decision(env, body, parents=(), ask=None):
    return F.card(env.conn, 'Synthetic decision', status='blocked', block_kind='needs_input', parents=parents,
                  body=body, human_request=ask or json.dumps(F.VALID_ASK))


def test_separate_decision_card_naming_the_held_card_covers_it(env):
    tid = card(env)
    decision(env, 'Decision only: follow-through work stays parked.')
    assert hits(env), 'a decision card that names no held card covers nothing'
    decision(env, f'Decision only: follow-through {tid} remains in triage.')
    assert hits(env) == []


def test_dependency_linked_decision_card_covers_its_parent(env):
    tid = card(env)
    decision(env, 'Decide the held work.', parents=[tid])
    assert hits(env) == []


def test_invalid_decision_card_naming_the_card_does_not_cover(env):
    tid = card(env)
    decision(env, f'About {tid}', ask='{"action":"vague"}')
    assert hits(env)


def test_canonical_safety_refusal_event_is_reported(env):
    from sr_helpers import PLUGIN, load
    tid = card(env, kind=None)
    F.event(env.conn, tid, 'spend_budget_triaged', {'cause': 'safety_refusal'}, at=env.now)
    f, = hits(env)
    assert f.evidence['red_cards'][0]['execution_refusal'] is True
    text = load('_sr_red_msg', PLUGIN / 'nudge.py').message(f.to_dict(), [])
    assert tid in text and '"execution_refusal": true' in text
    assert 'Safety/execution-refusal cards: no release' in text


def test_desktop_stopped_chat_is_not_resumed(env):
    card(env)
    env.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',
                      (F.CHAT, 'user', env.scan_mod.DESKTOP_STOP_MARKER, env.now + 1))
    env.state.commit()
    assert hits(env) == []
    env.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',
                      (F.CHAT, 'user', 'carry on', env.now + 2))
    env.state.commit()
    assert hits(env)

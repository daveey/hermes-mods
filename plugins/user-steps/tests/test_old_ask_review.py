"""Old-ask review: one local-board card per day lists undecided asks older than 72 h that nobody reviewed in the
last 7 days. The watcher never withdraws or decides anything; the reviewing worker uses the sanctioned
``--withdraw`` (by=hygiene, evidence required) or ``--reviewed`` CLI. All persistence is under tmp_path; the
Kanban tool is a stand-in except for one read-only check of the real kanban_create schema.
"""
import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('oar_' + name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core, sw = load('core'), load('stale_watch')
GW = 'install:local-test'
OWNER = {'gatewayId': GW, 'profile': 'default', 'storedSessionId': 'chat-1'}
NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC = 00:00 PST
H = 3600.0


def request(owner=OWNER, title='Approve the pricing change for the build?', aliases=None, refs=None):
    return dict(schemaVersion=2, owner=dict(owner), requestId=str(uuid.uuid4()), revision=1, kind='approval',
                title=title, scope=dict(markdown='Change example.py.', complete=True, sourceRefs=refs or []),
                limits=[], exclusions=[], outcomes=dict(approve='Do it', revise='Block', decline='Cancel'),
                choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')],
                aliases=aliases or [], state='open')


def make(store, age_h, owner=OWNER, **kw):
    return store.create(owner, request(owner, **kw), created_at=NOW - age_h * H)


def state_db(path, sessions=(), messages=()):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)')
        c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, timestamp REAL)')
        c.executemany('INSERT INTO sessions VALUES (?,?)', sessions)
        c.executemany('INSERT INTO messages(session_id, role, timestamp) VALUES (?,?,?)', messages)
    return path


def board_db(path, tasks=()):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, session_id TEXT, '
                  'completed_at INTEGER)')
        c.execute('CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, '
                  'created_at INTEGER)')
        c.executemany('INSERT INTO tasks VALUES (?,?,?,?,NULL)', tasks)
    return path


class Board:
    """Kanban stand-in with the real idempotency contract: one card per key."""
    def __init__(self):
        self.cards = {}

    def __call__(self, c):
        self.cards.setdefault(c['idempotency_key'], c)
        return 't_' + str(len(self.cards)).zfill(8)


@pytest.fixture
def env(tmp_path):
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    db = state_db(tmp_path / 'state.db', [('chat-1', 'Pricing chat')],
                  [('chat-1', 'user', NOW - 80 * H), ('chat-1', 'assistant', NOW - 79 * H)])
    boards = [('default', board_db(tmp_path / 'kanban.db', [('t_aaaaaaaa', 'Linked build card', 'blocked', 'chat-1'),
                                                             ('t_bbbbbbbb', 'Other chat card', 'done', 'chat-9')]))]
    return dict(store=store, db=db, boards=boards, tmp=tmp_path)


def review(env, board, now=NOW, **kw):
    kw.setdefault('local_gateway', GW)
    kw.setdefault('owner_db', lambda owner: env['db'] if owner['gatewayId'] == GW else None)
    kw.setdefault('boards', env['boards'])
    return sw.review_old_asks(core, env['store'], now=now, create=board, **kw)


def store_snapshot(store):
    with sqlite3.connect(store.path) as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: c.execute(f'SELECT * FROM {t} ORDER BY rowid').fetchall() for t in tables}


# ---- selection ------------------------------------------------------------------------------------------


def unstamped_revision(env, owner=OWNER):
    st = env['store']
    r = make(st, 200, owner=owner)
    st.revise(owner, r['requestId'], 1, r)
    with st._tx() as c:
        c.execute('DELETE FROM lifecycle')
    return r


def test_unstamped_revision_uses_old_owner_last_turn_bound(env):
    r = unstamped_revision(env)
    before = store_snapshot(env['store'])
    board = Board()
    assert review(env, board) == [r['requestId']]
    body = next(iter(board.cards.values()))['body']
    assert 'asked on or before 2027-01-11 17:00 PST' in body
    assert '(revision 2)' in body
    assert store_snapshot(env['store']) == before


@pytest.mark.parametrize('age', [1, 71, 72])
def test_unstamped_revision_waits_until_last_turn_bound_exceeds_72h(env, age):
    r = unstamped_revision(env)
    with sqlite3.connect(env['db']) as c:
        c.execute('UPDATE messages SET timestamp=?', (NOW - age * H,))
    assert review(env, Board()) == []
    assert review(env, Board(), now=NOW + (73 - age) * H) == [r['requestId']]


@pytest.mark.parametrize('case', ['foreign', 'unknown-profile', 'missing-session', 'no-messages', 'unreadable'])
def test_unstamped_revision_skips_unproven_owner_bound(env, case):
    r = unstamped_revision(env)
    # First prove the same request is eligible when its local owner is known.
    assert review(env, Board()) == [r['requestId']]
    kw = {}
    if case == 'foreign':
        kw['local_gateway'] = 'install:other'
    elif case == 'unknown-profile':
        kw['owner_db'] = lambda o: None
    elif case == 'unreadable':
        env['db'].write_bytes(b'not sqlite')
    else:
        with sqlite3.connect(env['db']) as c:
            c.execute('DELETE FROM ' + ('sessions' if case == 'missing-session' else 'messages'))
    assert review(env, Board(), **kw) == []


def test_only_undecided_asks_older_than_72h_are_listed(env):
    st = env['store']
    old = make(st, 80)
    make(st, 71)  # too young
    decided = make(st, 90, title='Approve the deploy window for Friday?')
    st.decide(OWNER, dict(requestId=decided['requestId'], expectedRevision=1, choiceId='approve',
                          idempotencyKey='k1', revisionText=None))
    gone = make(st, 90, title='Approve the old migration plan?')
    st.withdraw(OWNER, gone['requestId'], by='agent', reason='moot')
    bound = make(st, 90, title='Approve the card-bound step?', aliases=[
        dict(gatewayId=GW, profile='default', type='kanban', id='t_12345678', board='default')])
    ids = [i['requestId'] for i in sw.old_asks(core, st, now=NOW)]
    assert ids == [old['requestId']]
    assert bound['requestId'] not in ids


def test_selection_is_read_only(env):
    st = env['store']
    make(st, 80)
    before = store_snapshot(st)
    sw.old_asks(core, st, now=NOW)
    review(env, Board())
    assert store_snapshot(st) == before


def test_one_card_lists_every_old_ask_and_rerun_same_day_adds_none(env):
    st = env['store']
    a = make(st, 80)
    b = make(st, 200, title='Pick the logo variant for the site?')
    board = Board()
    assert sorted(review(env, board)) == sorted([a['requestId'], b['requestId']])
    review(env, board, now=NOW + 3 * H)
    assert len(board.cards) == 1
    key, c = next(iter(board.cards.items()))
    assert key == 'user-steps-old-asks:2027-01-15'
    assert a['requestId'] in c['body'] and b['requestId'] in c['body']
    assert 'Approve the pricing change for the build?' in c['body'] and 'Pick the logo variant' in c['body']
    review(env, board, now=NOW + 24 * H)
    assert list(board.cards) == ['user-steps-old-asks:2027-01-15', 'user-steps-old-asks:2027-01-16']


def test_daily_key_uses_pacific_date(env):
    make(env['store'], 80)
    board = Board()
    review(env, board, now=NOW - 1)  # 23:59:59 PST on Jan 14 (07:59:59 UTC Jan 15)
    assert list(board.cards) == ['user-steps-old-asks:2027-01-14']


def test_no_old_asks_creates_no_card(env):
    make(env['store'], 10)
    board = Board()
    assert review(env, board) == [] and board.cards == {}


# ---- card content ---------------------------------------------------------------------------------------

def test_card_shows_owner_chat_title_last_turn_pt_and_linked_card_states(env):
    r = make(env['store'], 80, title='Approve t_cccccccc rollout?')
    with sqlite3.connect(env['boards'][0][1]) as c:
        c.execute("INSERT INTO tasks VALUES ('t_cccccccc','Named rollout card','done','chat-x',NULL)")
    board = Board()
    review(env, board)
    body = next(iter(board.cards.values()))['body']
    assert r['requestId'] in body and 'Pricing chat' in body
    assert '2027-01-11 17:00 PST' in body  # last turn NOW-79h, Pacific
    assert 't_aaaaaaaa' in body and 'blocked' in body  # card linked to the owner chat
    assert 't_bbbbbbbb' not in body  # another chat's card
    assert 't_cccccccc' in body and 'done' in body  # card the ask names


def test_card_instructions_require_evidence_and_default_to_keep(env):
    make(env['store'], 80)
    board = Board()
    review(env, board)
    c = next(iter(board.cards.values()))
    body = c['body']
    assert '--withdraw' in body and '--evidence' in body and '--reviewed' in body
    for basis in ('done or archived', 'decided elsewhere', 'quote', 'expired', 'fixture', 'agent work'):
        assert basis in body
    assert 'otherwise' in body.lower() and 'keep' in body.lower()
    assert c['assignee'] == 'default'
    assert c['placement'] == 'local' and c['requires']


@pytest.mark.parametrize('case', ['foreign', 'unknown-profile', 'unreadable', 'missing-session'])
def test_unknown_or_unreadable_owner_chat_is_reported_not_guessed(env, case):
    owner = dict(OWNER, gatewayId='install:other') if case == 'foreign' else OWNER
    r = make(env['store'], 80, owner=owner)
    if case == 'unreadable':
        env['db'].write_bytes(b'not sqlite')
    if case == 'missing-session':
        with sqlite3.connect(env['db']) as c:
            c.execute('DELETE FROM sessions')
    owner_db = (lambda o: None) if case == 'unknown-profile' else None
    board = Board()
    review(env, board, **({'owner_db': owner_db} if owner_db else {}))
    body = next(iter(board.cards.values()))['body']
    assert r['requestId'] in body
    expected = {'foreign': 'on another gateway (not read)', 'unknown-profile': 'unknown profile (not read)',
                'unreadable': 'unreadable (', 'missing-session': 'not found in the owner store'}[case]
    assert expected in body
    assert store_snapshot(env['store'])['requests'][0][4] == 'open'


def test_unreadable_board_is_reported(env):
    make(env['store'], 80)
    env['boards'][0][1].write_bytes(b'not sqlite')
    board = Board()
    review(env, board)
    assert 'board default unreadable (' in next(iter(board.cards.values()))['body']


# ---- reviewed side table --------------------------------------------------------------------------------

def test_reviewed_request_is_excluded_for_seven_days(env):
    st = env['store']
    r = make(st, 80)
    out = sw.mark_reviewed(core, st, r['requestId'], reason='still a live pricing decision',
                           evidence='chat-1 asked it 80h ago; no reply yet', now=NOW)
    assert out['requestId'] == r['requestId'] and out['reviewedAt'] == NOW
    assert st.get(OWNER, r['requestId'])['state'] == 'open'  # keeping is not a decision
    assert sw.old_asks(core, st, now=NOW + 6 * 86400) == []
    assert [i['requestId'] for i in sw.old_asks(core, st, now=NOW + 7 * 86400 + 1)] == [r['requestId']]


def test_reviewed_requires_reason_and_an_open_request(env):
    st = env['store']
    r = make(st, 80)
    with pytest.raises(core.ActionError):
        sw.mark_reviewed(core, st, r['requestId'], reason='  ', evidence='x', now=NOW)
    with pytest.raises(core.ActionError):
        sw.mark_reviewed(core, st, 'no-such-id', reason='keep', evidence='x', now=NOW)
    st.withdraw(OWNER, r['requestId'], by='agent', reason='moot')
    with pytest.raises(core.ActionError):
        sw.mark_reviewed(core, st, r['requestId'], reason='keep', evidence='x', now=NOW)


# ---- sanctioned withdrawal ------------------------------------------------------------------------------

def test_withdraw_records_hygiene_actor_reason_and_evidence(env):
    st = env['store']
    r = make(st, 80)
    v = sw.withdraw_old(core, st, r['requestId'], reason='fixture request from a test run',
                        evidence='title matches tests/test_x.py fixture', local_gateway=GW, now=NOW)
    assert v['state'] == 'withdrawn'
    assert v['withdrawal']['by'] == 'hygiene'
    assert v['withdrawal']['reason'] == 'fixture request from a test run'
    assert 'tests/test_x.py' in v['withdrawal']['evidence']
    assert 'resolution' not in v
    with sqlite3.connect(st.path) as c:
        assert c.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


@pytest.mark.parametrize('missing', ['reason', 'evidence'])
def test_withdraw_requires_reason_and_evidence(env, missing):
    r = make(env['store'], 80)
    kw = dict(reason='expired window', evidence='window closed 2027-01-10')
    kw[missing] = ' '
    with pytest.raises(core.ActionError):
        sw.withdraw_old(core, env['store'], r['requestId'], local_gateway=GW, now=NOW, **kw)
    assert env['store'].get(OWNER, r['requestId'])['state'] == 'open'


def test_withdraw_refuses_recorded_decision(env):
    st = env['store']
    r = make(st, 80)
    st.decide(OWNER, dict(requestId=r['requestId'], expectedRevision=1, choiceId='approve',
                          idempotencyKey='k1', revisionText=None))
    before = st.view(OWNER, r['requestId'])
    with pytest.raises(core.ActionError):
        sw.withdraw_old(core, st, r['requestId'], reason='moot', evidence='x', local_gateway=GW, now=NOW)
    assert st.view(OWNER, r['requestId']) == before


def test_withdraw_refuses_card_bound_request(env):
    st = env['store']
    r = make(st, 80, aliases=[dict(gatewayId=GW, profile='default', type='kanban', id='t_12345678', board='default')])
    with pytest.raises(core.ActionError):
        sw.withdraw_old(core, st, r['requestId'], reason='moot', evidence='x', local_gateway=GW, now=NOW)
    assert st.get(OWNER, r['requestId'])['state'] == 'open'


@pytest.mark.parametrize('local', ['install:other', None])
def test_withdraw_refuses_foreign_or_unknown_gateway(env, local):
    st = env['store']
    r = make(st, 80)
    with pytest.raises(core.ActionError):
        sw.withdraw_old(core, st, r['requestId'], reason='moot', evidence='x', local_gateway=local, now=NOW)
    assert st.get(OWNER, r['requestId'])['state'] == 'open'


def test_withdraw_unknown_id_refused(env):
    with pytest.raises(core.ActionError):
        sw.withdraw_old(core, env['store'], 'nope', reason='moot', evidence='x', local_gateway=GW, now=NOW)


def test_cli_withdraw_and_reviewed(env, monkeypatch, capsys):
    st = env['store']
    a, b = make(st, 80), make(st, 90, title='Pick the logo variant for the site?')
    monkeypatch.setattr(sw, '_local_gateway', lambda core_mod: GW)
    monkeypatch.setattr(sw, '_store', lambda core_mod: st)
    monkeypatch.setattr(sw, '_ledger', lambda core_mod, owner: None)
    assert sw.main(['--withdraw', a['requestId'], '--reason', 'decided elsewhere',
                    '--evidence', 'user said "ship it" in chat-1']) == 0
    assert json.loads(capsys.readouterr().out)['withdrawn']['withdrawal']['by'] == 'hygiene'
    assert sw.main(['--reviewed', b['requestId'], '--reason', 'still live', '--evidence', 'asked 90h ago']) == 0
    assert json.loads(capsys.readouterr().out)['reviewed']['requestId'] == b['requestId']
    assert sw.main(['--withdraw', b['requestId'], '--reason', 'moot']) == 2  # evidence missing
    assert st.get(OWNER, b['requestId'])['state'] == 'open'


# ---- card creation goes through the kanban_create tool handler, pinned local ---------------------------

class FakeRegistry:
    def __init__(self, props, result='{"ok": true, "task_id": "t_0000abcd"}'):
        self.props, self.result, self.calls = props, result, []
        outer = self

        class Entry:
            @staticmethod
            def handler(args, **kw):
                outer.calls.append(args)
                return outer.result
        self.entry = Entry

    def get_entry(self, name, **kw):
        return self.entry if name == 'kanban_create' else None

    def get_schema(self, name):
        return {'parameters': {'properties': self.props}} if name == 'kanban_create' else None


PROPS = {k: {} for k in ('title', 'body', 'assignee', 'idempotency_key', 'placement', 'requires')}


def test_create_review_card_invokes_kanban_create_handler_local(monkeypatch):
    monkeypatch.setattr(sw.subprocess, 'run', lambda *a, **k: pytest.fail('no hermes kanban CLI'))
    reg = FakeRegistry(PROPS)
    c = sw.review_card([], NOW)
    assert sw.create_review_card(c, registry=reg) == 't_0000abcd'
    (args,) = reg.calls
    assert args['placement'] == 'local' and args['requires'] == c['requires'] and args['requires']
    assert args['idempotency_key'] == c['idempotency_key'] and args['title'] == c['title']
    assert set(args) <= set(PROPS)


@pytest.mark.parametrize('missing', ['placement', 'requires'])
def test_create_review_card_refuses_tool_without_placement_or_requires(missing):
    reg = FakeRegistry({k: v for k, v in PROPS.items() if k != missing})
    with pytest.raises(RuntimeError):
        sw.create_review_card(sw.review_card([], NOW), registry=reg)
    assert reg.calls == []


def test_create_review_card_raises_on_tool_error():
    reg = FakeRegistry(PROPS, result='{"error": "placement_unavailable"}')
    with pytest.raises(RuntimeError):
        sw.create_review_card(sw.review_card([], NOW), registry=reg)


def test_real_kanban_create_schema_declares_placement_and_requires():
    pytest.importorskip('tools.kanban_tools')
    from tools.registry import registry
    assert registry.get_entry('kanban_create') is not None
    props = registry.get_schema('kanban_create')['parameters']['properties']
    assert {'placement', 'requires', 'idempotency_key', 'assignee', 'body', 'title'} <= set(props)


@pytest.mark.parametrize('action', ['--withdraw', '--reviewed'])
def test_dry_run_refuses_mutating_actions(env, monkeypatch, capsys, action):
    r = make(env['store'], 80)
    before = store_snapshot(env['store'])
    monkeypatch.setattr(sw, '_store', lambda c: env['store'])
    monkeypatch.setattr(sw, '_ledger', lambda *a: None)
    monkeypatch.setattr(sw, '_local_gateway', lambda c: GW)
    assert sw.main(['--dry-run', action, r['requestId'], '--reason', 'keep', '--evidence', 'checked']) == 2
    assert 'dry-run' in json.loads(capsys.readouterr().out)['error']
    assert store_snapshot(env['store']) == before


def test_original_legacy_ledger_age_creates_review_without_stamping(env, monkeypatch):
    st = env['store']
    alias = dict(type='user_step', id='legacy-step', gatewayId=GW, profile='default')
    r = make(st, 80, aliases=[alias])
    with st._tx() as c:
        c.execute('UPDATE lifecycle SET created_at=NULL, asked_revision=NULL')
    ledger = core.Ledger(env['tmp'] / 'ledger', db=env['db'])
    with ledger.edit('chat-1') as d:
        d['steps'] = [dict(id='legacy-step', text='Approve pricing?', created=NOW - 100 * H)]
    monkeypatch.setattr(sw, '_review_ledger', lambda *a: ledger, raising=False)
    monkeypatch.setattr(ledger, 'root', lambda sid: sid)
    before = store_snapshot(st)
    ledger_before = ledger.path('chat-1').read_bytes()
    board = Board()
    assert review(env, board) == [r['requestId']]
    assert sw.old_asks(core, st, now=NOW)[0]['askedAt'] == NOW - 100 * H
    assert store_snapshot(st) == before
    assert ledger.path('chat-1').read_bytes() == ledger_before
    # Original evidence cannot date an unstamped later revision.
    st.revise(OWNER, r['requestId'], 1, r)
    with st._tx() as c:
        c.execute('UPDATE lifecycle SET created_at=NULL, asked_revision=NULL')
    assert sw.old_asks(core, st, now=NOW) == []


def test_old_review_does_not_suppress_new_revision(env):
    st = env['store']
    r = make(st, 200)
    sw.mark_reviewed(core, st, r['requestId'], reason='still needed', now=NOW - 100 * H)
    st.revise(OWNER, r['requestId'], 1, r)
    with st._tx() as c:
        c.execute('UPDATE lifecycle SET created_at=?, asked_revision=2', (NOW - 80 * H,))
    assert [i['requestId'] for i in sw.old_asks(core, st, now=NOW)] == [r['requestId']]


def test_linked_cards_include_real_notify_subscriptions(env):
    with sqlite3.connect(env['boards'][0][1]) as c:
        c.execute('CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT, notifier_profile TEXT)')
        c.execute("INSERT INTO kanban_notify_subs VALUES ('t_bbbbbbbb','tui','chat-1','default')")
    cards, notes = sw.linked_cards(core, request(), OWNER, env['boards'])
    assert {c['id'] for c in cards} == {'t_aaaaaaaa', 't_bbbbbbbb'}
    assert notes == []


def test_named_cards_survive_missing_chat_link_schema(env):
    path = env['tmp'] / 'older-board.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE tasks(id TEXT, title TEXT, status TEXT)')
        c.execute("INSERT INTO tasks VALUES ('t_cccccccc','Named card','done')")
    cards, notes = sw.linked_cards(core, request(title='Review t_cccccccc?'), OWNER, [('old', path)])
    assert [c['id'] for c in cards] == ['t_cccccccc']
    assert any('chat links' in n for n in notes)


@pytest.mark.parametrize('field,value', [('id', 'other-step'), ('profile', 'other-profile'),
                                          ('gatewayId', 'install:other')])
def test_legacy_fallback_requires_exact_owner_alias(env, monkeypatch, field, value):
    ledger = core.Ledger(env['tmp'] / 'ledger', db=env['db'])
    with ledger.edit('chat-1') as d:
        d['steps'] = [dict(id='legacy-step', text='Approve?', created=NOW - 100 * H)]
    monkeypatch.setattr(sw, '_review_ledger', lambda *a: ledger)
    monkeypatch.setattr(ledger, 'root', lambda sid: sid)
    alias = dict(type='user_step', id='legacy-step', gatewayId=GW, profile='default')
    alias[field] = value
    r = make(env['store'], 80, aliases=[alias])
    with env['store']._tx() as c:
        c.execute('UPDATE lifecycle SET created_at=NULL, asked_revision=NULL')
    assert sw.old_asks(core, env['store'], now=NOW) == []


def test_dry_run_store_never_initializes_and_rejects_writes(env, monkeypatch):
    home = env['tmp'] / 'read-home'
    (home / 'user-steps').mkdir(parents=True)
    path = home / 'user-steps' / 'actions.sqlite3'
    with sqlite3.connect(env['store'].path) as source, sqlite3.connect(path) as target:
        source.backup(target)
    monkeypatch.setattr(core, 'home', lambda: home)
    monkeypatch.setattr(core.ActionStore, '__init__', lambda *a, **k: pytest.fail('no migrations on dry-run'))
    st = sw._read_only_store(core)
    for read in (st._read, st._tx):
        with read() as c:
            assert c.execute('PRAGMA query_only').fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError):
                c.execute('DELETE FROM requests')


def test_cli_dry_run_uses_read_only_store(env, monkeypatch, capsys):
    sentinel = object()
    monkeypatch.setattr(sw, '_read_only_store', lambda c: sentinel)
    monkeypatch.setattr(sw, '_store', lambda c: pytest.fail('no writable store'))
    def run(c, chat, store, **kw):
        assert store is sentinel
        return []
    monkeypatch.setattr(sw, 'run', run)
    assert sw.main(['--dry-run']) == 0
    assert json.loads(capsys.readouterr().out) == {'cards': []}


def test_run_without_review_creator_skips_review(env):
    make(env['store'], 80)
    board = Board()
    sw.run(core, load('chat'), env['store'], env['tmp'] / 'missing.db', now=NOW, create=board)
    assert not any(k.startswith('user-steps-old-asks:') for k in board.cards)

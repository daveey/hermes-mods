"""A hub Kanban worker's spend approval lives in its card's origin chat on another gateway.

Live false hold (hermes-hub, card t_01a193e011b0a776, runs 17295 and 25359): David answered the card's verbatim
ask "Approve or decline $36 total for DC/MS on one L40S ..." with "approved" in the origin chat
@session:default@macbook/<id> on 10-31. The worker runs on the hub, so neither its live window nor its own persisted
transcript holds the pair, and the coach held its "$36" spend lines. The check now reads the card's origin chat
(the `platform='hub'` notify sub with reason `origin`) through the session-mentions Desktop relay (`authority_read`),
and credits only a typed yes to a priced ask there. Card bodies and agent relays never count.
"""
import importlib.util
import json
import threading
import types
import time

import pytest
from test_compute_authority import RELAYED_ASK, call
from test_compute_envelope import HERE, _log

SPEND_LINE = 'Spend so far is $0.56 of the $36, and Vast shows exactly one GPU running.'
BIGGER = 'I will rent one L40S with a $100 cap.'
ORIGIN_SID = '20261026_154948_ea6cfd'


def _authority_rows(messages):
    """Python mirror of the Desktop courier's `authorityRows` (desktop-plugins/session-mentions/plugin.js; its
    node test checks the JS against the same shapes): every user row plus the last assistant text row before it."""
    def text(m):
        c = m.get('content')
        if isinstance(c, list):
            c = ' '.join(p.get('text', '') for p in c if isinstance(p, dict) and p.get('type') == 'text')
        return c if isinstance(c, str) else ''

    def slim(m):
        t = text(m)
        t = t[:2000] if m['role'] == 'user' else (t if len(t) <= 6300 else f'{t[:300]}\n…\n{t[-6000:]}')
        out = {'id': m.get('id'), 'role': m['role'], 'content': t}
        out.update({k: m[k] for k in ('display_kind', 'observed', 'timestamp', 'display_metadata')
                    if m.get(k) not in (None, '')})
        out.update({k: True for k in m if k.startswith('_') and m[k]})
        return out

    out, pending = [], None
    for m in messages:
        if m.get('role') == 'assistant':
            if text(m).strip() or m.get('observed'):
                pending = m
        elif m.get('role') == 'user':
            if pending:
                out.append(slim(pending))
            pending = None
            out.append(slim(m))
    return out


class Courier:
    """The MacBook Desktop's courier draining the hub's session-mentions outbox: it serves `authority_read` for
    gateway `macbook` from that gateway's own SessionDB, the way the Desktop pages /api/sessions/<id>/messages."""

    def __init__(self, base, macbook_db, *, alive=True, stale_desktop=False):
        self.base, self.db, self.stale = base, macbook_db, stale_desktop
        self.requests = []
        for sub in ('outbox', 'results', 'couriers'):
            (base / sub).mkdir(parents=True, exist_ok=True)
        if alive:
            (base / 'couriers' / 'macbook.json').write_text(json.dumps(
                {'device': 'macbook', 'aliases': ['macbook'], 'reach': ['macbook', 'hub'], 'home': False,
                 'at': time.time()}))
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        from hermes_state import SessionDB
        while not self.stop.is_set():
            for path in sorted((self.base / 'outbox').glob('*.json')):
                req = json.loads(path.read_text())
                path.unlink()
                self.requests.append(req)
                if self.stale or req['action'] != 'authority_read':
                    res = {'ok': False, 'error': f"unknown action {req['action']}"}
                else:
                    assert req['route'] == 'macbook'
                    sid = req['args']['target'].rsplit('/', 1)[1]
                    db = SessionDB(self.db, read_only=True)
                    try:
                        rows = db.get_messages(sid, include_compacted=True, include_ancestors=True)
                    finally:
                        db.close()
                    res = {'ok': True, 'messages': _authority_rows(rows)}
                (self.base / 'results' / f"{req['id']}.json").write_text(json.dumps(res))
            time.sleep(0.05)

    def close(self):
        self.stop.set()
        self.thread.join(2)


def _macbook_chat(path, rows):
    """The origin chat on the MacBook's own store; `rows` = (role, content) pairs, then compaction."""
    from hermes_state import SessionDB
    from agent.context_compressor import SUMMARY_PREFIX
    db = SessionDB(path)
    try:
        db.create_session(ORIGIN_SID, source='desktop')
        for role, content in rows:
            db.append_message(ORIGIN_SID, role, content)
        db.archive_and_compact(ORIGIN_SID, [{'role': 'user', 'content': f'{SUMMARY_PREFIX}\nEarlier turns.'}],
                               tail_count=0)
    finally:
        db.close()


APPROVED = [('user', 'run stage b, you may rent computed'), ('assistant', RELAYED_ASK), ('user', 'approved'),
            ('assistant', 'Approved: the rerun card is unblocked.')]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """This process is a Kanban worker on gateway `hub`; its card's origin chat is on gateway `macbook`."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kn

    monkeypatch.setenv('COACH_REPLY_RULES', 'none')
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path / 'kanban'))
    (tmp_path / 'kanban').mkdir()
    db_path = kb.init_db(tmp_path / 'kanban' / 'kanban.db')
    with kbc.connect_closing(db_path) as conn:
        card = kb.create_task(conn, title='Decision: cohort-X DC/MS rerun, one L40S, $36 envelope',
                              body='David approved $36 for this rerun.', assignee='default')
        kn.add_notify_sub(conn, task_id=card, platform='hub', chat_id=f'macbook/default/{ORIGIN_SID}',
                          delivery_metadata={'reason': 'origin'})
        plain = kb.create_task(conn, title='same body, no origin chat', body='David approved $36 for this rerun.',
                               assignee='default')
    monkeypatch.setenv('HERMES_KANBAN_DB', str(db_path))
    monkeypatch.setenv('HERMES_KANBAN_TASK', card)

    spec = importlib.util.spec_from_file_location('coach_init_origin', HERE / '__init__.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = mod._load('core').Store(root=tmp_path / 'coach')
    _, hooks = mod.build(store=store, checkout=None)
    oa = mod._load('origin_authority')
    oa._cache.clear()
    relay = tmp_path / 'relay'
    monkeypatch.setattr(oa, '_relay_root', lambda: relay)
    monkeypatch.setattr(oa, 'this_gateway', lambda: 'hub')
    monkeypatch.setattr(oa, 'PICKUP_S', 1.0)
    couriers = []

    h = types.SimpleNamespace()
    h.hooks, h.oa, h.relay, h.card, h.plain, h.tmp = hooks, oa, relay, card, plain, tmp_path
    h.macbook_db = tmp_path / 'macbook' / 'state.db'
    h.macbook_db.parent.mkdir()

    def courier(**kw):
        c = Courier(relay, h.macbook_db, **kw)
        couriers.append(c)
        return c
    h.courier = courier
    # The worker's own session: a Kanban brief, no user approval of its own.
    hooks['pre_llm_call'](session_id='authority', user_message='work kanban task ' + card,
                          conversation_history=[{'role': 'user', 'content': 'work kanban task ' + card}])
    yield h
    for c in couriers:
        c.close()


def _reads(tmp_path):
    return [r for r in _log(tmp_path) if r.get('event') == 'authority_origin_read']


@pytest.mark.parametrize('surface', ['reply', 'kanban_create', 'kanban_block'])
def test_hub_worker_credits_the_cross_gateway_origin_approval(hub, surface):
    _macbook_chat(hub.macbook_db, APPROVED)
    courier = hub.courier()
    assert call(hub.hooks, surface, SPEND_LINE) is None
    # The origin yes credits only the asked figure: a larger cap is still held.
    assert call(hub.hooks, surface, BIGGER)
    reads = _reads(hub.tmp)
    assert reads and reads[0]['ok'] and reads[0]['usd'] == [36.0]
    assert reads[0]['origin'] == f'@session:default@macbook/{ORIGIN_SID}' and reads[0]['session_id'] == 'authority'
    assert [r['action'] for r in courier.requests] == ['authority_read']  # cached: one read for both holds
    assert courier.requests[0]['args']['target'] == f'@session:default@macbook/{ORIGIN_SID}'
    assert not list((hub.relay / 'outbox').glob('*.json'))


def test_card_body_is_not_authority(hub, monkeypatch):
    """Same body ("David approved $36"), no origin chat: nothing to credit, so the spend line is held."""
    _macbook_chat(hub.macbook_db, APPROVED)
    courier = hub.courier()
    monkeypatch.setenv('HERMES_KANBAN_TASK', hub.plain)
    assert call(hub.hooks, 'reply', SPEND_LINE)
    assert courier.requests == []


@pytest.mark.parametrize('rows', [
    # an agent session relayed "approved" into the origin chat
    [('assistant', RELAYED_ASK), ('user', '[Message from another Hermes session "worker"]\n\napproved')],
    # the agent itself says David approved
    [('user', 'status?'), ('assistant', 'David approved $36 total for DC/MS on one L40S.'), ('user', 'ok')],
    # a yes to an unpriced question
    [('assistant', 'Should I keep monitoring the rerun?'), ('user', 'approved')],
])
def test_origin_chat_credits_only_a_typed_yes_to_a_priced_ask(hub, rows):
    _macbook_chat(hub.macbook_db, rows)
    hub.courier()
    assert call(hub.hooks, 'reply', SPEND_LINE)
    reads = _reads(hub.tmp)
    assert reads and reads[0]['ok'] and reads[0]['usd'] == []


@pytest.mark.parametrize('kw, error', [({'alive': False}, 'no live Desktop courier'),
                                        ({'stale_desktop': True}, 'unknown action')])
def test_unreadable_origin_credits_nothing_and_is_logged(hub, kw, error):
    _macbook_chat(hub.macbook_db, APPROVED)
    hub.courier(**kw)
    assert call(hub.hooks, 'reply', SPEND_LINE)
    reads = _reads(hub.tmp)
    assert reads and reads[0]['ok'] is False and error in reads[0]['error']
    assert not list((hub.relay / 'outbox').glob('*.json'))


def test_unclaimed_read_times_out_and_withdraws(hub, monkeypatch):
    """A courier that is alive but never claims (it cannot reach the gateway): the request is withdrawn."""
    _macbook_chat(hub.macbook_db, APPROVED)
    (hub.relay / 'couriers').mkdir(parents=True)
    (hub.relay / 'couriers' / 'macbook.json').write_text(json.dumps({'device': 'macbook', 'reach': ['macbook']}))
    started = time.time()
    assert call(hub.hooks, 'reply', SPEND_LINE)
    assert time.time() - started < 5
    assert 'claimed' in _reads(hub.tmp)[0]['error']
    assert not list((hub.relay / 'outbox').glob('*.json'))


def test_recorded_origin_read_credits_while_the_origin_gateway_sleeps(hub):
    """Live: the MacBook slept (its courier stale, the mini's SSH hop timed out), so a fresh hub run could not read
    the origin. A successful earlier read is on record and still credits only the approved figure."""
    _macbook_chat(hub.macbook_db, APPROVED)
    courier = hub.courier()
    assert call(hub.hooks, 'reply', SPEND_LINE) is None
    courier.close()
    (hub.relay / 'couriers' / 'macbook.json').unlink()
    record = json.loads((hub.tmp / 'coach' / 'origin_authority.json').read_text())
    assert record[f'@session:default@macbook/{ORIGIN_SID}']['usd'] == [36.0]
    hub.oa._cache.clear()  # a new Kanban run is a new process
    assert call(hub.hooks, 'reply', SPEND_LINE) is None
    assert call(hub.hooks, 'kanban_create', BIGGER)
    last = _reads(hub.tmp)[-1]
    assert last['ok'] is False and 'no live Desktop courier' in last['error'] and last['recorded_usd'] == [36.0]


def test_a_fresh_read_replaces_the_record(hub):
    (hub.tmp / 'coach').mkdir(exist_ok=True)
    (hub.tmp / 'coach' / 'origin_authority.json').write_text(json.dumps(
        {f'@session:default@macbook/{ORIGIN_SID}': {'at': time.time(), 'usd': [36.0]}}))
    _macbook_chat(hub.macbook_db, [('user', 'status?'), ('assistant', 'Rerun is idle.'), ('user', 'ok')])
    hub.courier()
    assert call(hub.hooks, 'reply', SPEND_LINE)
    record = json.loads((hub.tmp / 'coach' / 'origin_authority.json').read_text())
    assert record[f'@session:default@macbook/{ORIGIN_SID}']['usd'] == []


def test_an_expired_record_credits_nothing(hub, monkeypatch):
    (hub.tmp / 'coach').mkdir(exist_ok=True)
    (hub.tmp / 'coach' / 'origin_authority.json').write_text(json.dumps(
        {f'@session:default@macbook/{ORIGIN_SID}': {'at': time.time() - hub.oa.DURABLE_TTL_S - 1, 'usd': [36.0]}}))
    assert call(hub.hooks, 'reply', SPEND_LINE)
    assert 'recorded_usd' not in _reads(hub.tmp)[-1]


def test_not_a_worker_reads_no_origin(hub, monkeypatch):
    monkeypatch.delenv('HERMES_KANBAN_TASK')
    _macbook_chat(hub.macbook_db, APPROVED)
    courier = hub.courier()
    assert call(hub.hooks, 'reply', SPEND_LINE)
    assert courier.requests == [] and _reads(hub.tmp) == []


def test_card_origins_reads_only_origin_hub_subs(hub):
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kn
    with kbc.connect_closing(hub.tmp / 'kanban' / 'kanban.db') as conn:
        kn.add_notify_sub(conn, task_id=hub.card, platform='hub', chat_id='mini/default/s_shepherd',
                          delivery_metadata={'rel': 'shepherd'})
        kn.add_notify_sub(conn, task_id=hub.card, platform='tui', chat_id='s_local')
    assert hub.oa.card_origins(hub.card) == [('macbook', 'default', ORIGIN_SID)]
    assert hub.oa.card_origins(hub.plain) == []

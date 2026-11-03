"""Producer admission makes one core store open per failed attempt, same outcomes.

A completion whose session is gateway-routed and whose owner is not available keeps
failing until the owner is back; each retry (after its backoff) must still reach
core, but the read that only repeated the admission call's own in-transaction
lookup is not repeated work worth an extra writable SessionDB open.
"""
import json
import os
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_core  # tests/conftest.py: skipped when no Hermes core is importable
from test_closed_asks import ut

comp = ut._completion_module()
SID_ROUTED, SID_LOCAL = 'routed-session', 'local-session'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_USER_TERMINAL_DIR', str(tmp_path / 'jobs'))
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.setattr(ut, 'confirm_mode', lambda: 'off')
    monkeypatch.setattr(ut, 'max_wait_minutes', lambda: 120)
    monkeypatch.setattr(ut, 'reattach_grace_minutes', lambda: 10)
    from hermes_cli import plugins
    monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
    return tmp_path


def make_store(home):
    from hermes_state import SessionDB
    db = SessionDB(home / 'state.db')
    db.create_session(SID_ROUTED, source='telegram', session_key='agent:main:telegram:dm:1')
    db.create_session(SID_LOCAL, source='tui')
    db.close()


def route(home, sid):
    con = sqlite3.connect(home / 'state.db')
    con.execute('UPDATE sessions SET session_key = ? WHERE id = ?', ('agent:main:telegram:dm:9', sid))
    con.commit()
    con.close()


def count_opens(monkeypatch):
    import hermes_state_producer_admission as ingress
    real, opens = ingress.open_profile_db, []
    def counted(profile_home):
        opens.append(str(profile_home))
        return real(profile_home)
    monkeypatch.setattr(ingress, 'open_profile_db', counted)
    return opens


def expire_backoff(job):
    path = job / 'completion-pending.json'
    state = json.loads(path.read_text())
    state['next_at'] = 0
    path.write_text(json.dumps(state))


@pytest.mark.parametrize('entry', ['sweep', 'watch_once'])
def test_routed_retry_opens_core_store_once_per_attempt(home, monkeypatch, entry):
    make_store(home)
    job = ut.create_job('true', 'routed fixture', str(home), owner={'kind': 'session', 'session_id': SID_ROUTED})
    (job / 'output.log').write_text('routed result')
    ut._take_final(job, {'status': 'ran', 'exit_code': 0})
    opens = count_opens(monkeypatch)
    run = ut.sweep_jobs if entry == 'sweep' else (lambda: ut.watch_once(job, {}))
    for attempt in (1, 2, 3):
        run()
        pending = json.loads((job / 'completion-pending.json').read_text())
        # Every attempt still reaches core and still fails/backs off as before.
        assert pending['attempts'] == attempt
        assert (pending['kind'], pending['reason']) == ('transient', 'gateway owner unavailable')
        assert not (job / 'completion-receipt.json').exists()
        assert len(opens) == attempt  # one profile-store open per attempt, not two
        run()  # inside the backoff: no core access at all
        assert len(opens) == attempt
        expire_backoff(job)


def reference_admit_producer(payload, home):
    """The pre-change admission sequence, verbatim (read, admit, read back)."""
    from hermes_state_producer_admission import (
        admit_producer_completion, read_producer_admission, payload_digest)
    args = {'producer': payload['identity'], 'profile_home': Path(home)}
    result = read_producer_admission(**args)
    if result['outcome'] == 'absent':
        result = admit_producer_completion(**args, stored_session_id=payload['session_id'], text=payload['text'])
        if result['outcome'] == 'admitted':
            result = read_producer_admission(**args)
    outcome = result['outcome']
    if outcome == 'admitted':
        if result.get('payload_digest') != payload_digest(payload['text']):
            raise comp.Blocked('admission_payload_mismatch')
        return {'status': 'admitted', 'session_id': result['owner'],
                'producer': payload['identity'], 'ingress': 'producer_completion'}
    if outcome == 'routed':
        return None
    if outcome == 'missing':
        return {'status': 'missing', 'session_id': payload['session_id'], 'reason': result.get('reason')}
    if outcome == 'retry':
        raise comp.Unavailable('producer_' + result.get('reason', 'retry'))
    raise comp.Unavailable('producer_admission_not_committed')


IDENTITY = json.dumps(['user-terminal', 'job-1', 'final'], separators=(',', ':'))
OTHER = 'an earlier, different notice'


def scenario(name, home):
    """Build *name*'s store in *home*; return the payload delivered against it."""
    from hermes_state_producer_admission import admit_producer_completion
    sid, text, identity = SID_LOCAL, 'the final notice', IDENTITY
    if name != 'no_store':
        make_store(home)
    def prior(prior_text, prior_sid=SID_LOCAL):
        assert admit_producer_completion(producer=IDENTITY, profile_home=home, stored_session_id=prior_sid,
                                         text=prior_text)['outcome'] == 'admitted'
    if name == 'routed':
        sid = SID_ROUTED
    elif name == 'missing_session':
        sid = 'no-such-session'
    elif name == 'prior_same':
        prior(text)
    elif name == 'prior_mismatch':
        prior(OTHER)
    elif name == 'prior_now_routed':
        prior(text)
        route(home, SID_LOCAL)
    elif name == 'prior_now_routed_mismatch':
        prior(OTHER)
        route(home, SID_LOCAL)
    elif name == 'prior_mismatch_invalid_text':
        prior(OTHER)
        text = ' '
    elif name == 'invalid_text':
        text = '\x00'
    elif name == 'invalid_producer':
        identity = '["not-trusted","x"]'
    elif name == 'storage_retry':
        pass  # applied by the caller around both calls
    return {'session_id': sid, 'identity': identity, 'text': text}


def observe(call, payload, home, monkeypatch, name):
    import hermes_state_producer_admission as ingress
    with monkeypatch.context() as patcher:
        if name == 'storage_retry':
            def busy(profile_home):
                raise sqlite3.OperationalError('database is locked')
            patcher.setattr(ingress, 'open_profile_db', busy)
        try:
            outcome = ('ok', call(payload, home))
        except Exception as exc:
            outcome = ('raised', type(exc).__name__, str(exc))
    rows = []
    path = home / 'state.db'
    if path.exists():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='producer_admissions'").fetchone():
            rows = [{k: r[k] for k in r.keys() if k not in ('created_at', 'settled_at')}
                    for r in con.execute('SELECT * FROM producer_admissions ORDER BY producer')]
        con.close()
    return outcome, rows


@pytest.mark.parametrize('name', ['unrouted', 'routed', 'missing_session', 'no_store', 'prior_same', 'prior_mismatch',
                                  'prior_now_routed', 'prior_now_routed_mismatch', 'prior_mismatch_invalid_text',
                                  'invalid_text', 'invalid_producer', 'storage_retry'])
def test_admission_outcomes_match_reference_sequence(tmp_path, monkeypatch, name):
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    observed = {}
    for label, call in (('reference', reference_admit_producer), ('current', comp.admit_producer)):
        home = tmp_path / label
        home.mkdir()
        monkeypatch.setenv('HERMES_HOME', str(home))
        payload = scenario(name, home)
        observed[label] = observe(call, payload, home, monkeypatch, name)
    assert observed['current'] == observed['reference']
    expected = {'unrouted': 'ok', 'routed': 'ok', 'missing_session': 'ok', 'no_store': 'ok', 'prior_same': 'ok',
                'prior_mismatch': 'raised', 'prior_now_routed': 'ok', 'prior_now_routed_mismatch': 'ok',
                'prior_mismatch_invalid_text': 'raised', 'invalid_text': 'raised', 'invalid_producer': 'raised',
                'storage_retry': 'raised'}[name]
    assert observed['current'][0][0] == expected, observed  # the case exercises the branch it names

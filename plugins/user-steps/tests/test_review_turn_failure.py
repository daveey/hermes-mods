"""Review regression: storage recovery must not restore old-turn authority."""
import json
from contextlib import contextmanager
import pytest
import test_one_open_ask
from test_confirm_plan import PLAN, ASK, desktop_sources
from test_decisions import decision

@pytest.fixture
def plugin(monkeypatch, tmp_path):
    import sys
    for key in list(sys.modules):
        if key.startswith('user_steps_'):
            monkeypatch.delitem(sys.modules, key)
    return test_one_open_ask.plugin.__wrapped__(tmp_path, monkeypatch)

@pytest.mark.parametrize('fail_reset', [False, True])
def test_review_failed_turn_reset_cannot_reuse_old_decide(plugin, monkeypatch, tmp_path, desktop_sources, fail_reset):
    sid = 'session-a'
    own = plugin.core.server_owner(sid)
    ledger = plugin.core.Ledger(tmp_path / 'review-ledger', db=tmp_path / 'missing.db')
    store = plugin.core.ActionStore(tmp_path / 'review-actions.db')
    monkeypatch.setattr(plugin, '_ledger', lambda: ledger)
    plugin.core.begin_publication_turn(ledger, sid)
    old = plugin.core.add_step(sid, ASK, ['Go', 'Change plan'], ledger=ledger)
    original = ledger.edit
    @contextmanager
    def failing(root):
        raise OSError('fixture transient reset write failed')
        yield
    if fail_reset:
        monkeypatch.setattr(ledger, 'edit', failing)
    plugin.pre_llm_call(session_id=sid, platform='desktop', user_message='new unrelated turn')
    monkeypatch.setattr(ledger, 'edit', original)
    text = PLAN + '\n\n::user-step{id="' + old['id'] + '" s="' + sid + '" t="' + plugin.core.enc(ASK) + '"}'
    llm = lambda *_: json.dumps({'steps': [{'text': ASK, 'kind': 'approve', 'verbatim_span': ASK}], 'resolved': []})
    plugin.core.process_reply(text, sid, ledger=ledger, llm=llm, action_store=store, action_owner=own)
    authorized = []
    for r in store.requests(own):
        if any(c['id'] == 'go' for c in r['choices']):
            authorized.append(store.decide(own, decision(r, choice='go'))['operation']['intent']['executionAuthorized'])
    assert not any(authorized)


@pytest.mark.parametrize('fault', ['read', 'missing', 'stale', 'write', 'restart'])
@pytest.mark.parametrize('producer', [False, True])
def test_marker_fault_is_sticky_after_storage_recovers(tmp_path, monkeypatch, desktop_sources, fault, producer):
    from test_confirm_plan import core, OWNER, directive
    ledger = core.Ledger(tmp_path / 'ledger', db=tmp_path / 'missing.db')
    sid = OWNER['storedSessionId']
    token = core.begin_publication_turn(ledger, sid)
    step = core.add_step(sid, ASK, ['Go', 'Change plan'], ledger=ledger) if producer else None
    if fault == 'read':
        with monkeypatch.context() as m:
            m.setattr(ledger, '_read_strict', lambda *_: (_ for _ in ()).throw(OSError('read failed')))
            assert core.current_publication_turn(ledger, sid) is None
    elif fault in ('missing', 'stale'):
        with ledger.edit(ledger.root(sid)) as data:
            data['publication_turn'] = None if fault == 'missing' else 'old-turn'
        assert core.current_publication_turn(ledger, sid) is None
    elif fault == 'write':
        @contextmanager
        def failing(*_):
            raise OSError('write failed')
            yield
        with monkeypatch.context() as m:
            m.setattr(ledger, 'edit', failing)
            with pytest.raises(OSError):
                core.begin_publication_turn(ledger, sid)
    else:
        core._PUBLICATION_TURNS.clear()  # disk alone is not process custody
    # Even the exact previous valid bytes cannot resurrect this turn.
    with ledger.edit(ledger.root(sid)) as data:
        data['publication_turn'] = token
    store = core.ActionStore(tmp_path / 'actions.db')
    llm = lambda *_: json.dumps({'steps': [{'text': ASK, 'kind': 'approve', 'verbatim_span': ASK}], 'resolved': []})
    core.process_reply(PLAN + '\n\n' + directive(step), sid, ledger=ledger, llm=llm,
                       action_store=store, action_owner=OWNER)
    assert not any(r['scope']['complete'] for r in store.requests(OWNER))
    assert not any(c['effect'] == 'approve' for r in store.requests(OWNER) for c in r['choices'])
    assert core.current_publication_turn(ledger, sid) is None


def test_sibling_core_loader_shares_successful_turn_custody(tmp_path, desktop_sources):
    import importlib.util
    from test_confirm_plan import core, OWNER, directive
    spec = importlib.util.spec_from_file_location('review_session_outcome_core', core.__file__)
    sibling = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sibling)
    ledger = core.Ledger(tmp_path / 'ledger', db=tmp_path / 'missing.db')
    sid = OWNER['storedSessionId']
    token = core.begin_publication_turn(ledger, sid)
    step = sibling.add_step(sid, ASK, ['Go', 'Change plan'], ledger=ledger)
    assert step['publication_turn'] == token
    store = core.ActionStore(tmp_path / 'actions.db')
    core.attach_confirmation_plans(PLAN + '\n\n' + directive(step), sid, ledger, store, OWNER)
    assert store.requests(OWNER)[0]['scope']['complete']

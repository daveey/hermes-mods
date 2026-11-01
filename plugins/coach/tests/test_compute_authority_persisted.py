"""A user's priced approval outlives context compaction.

Live false hold (2026-11-01 13:59 PST): David answered the relayed "Approve or decline $36 total ..." ask with
"approved" on 10-31; compaction later moved both rows out of the live window, so `answered_asks` (window only)
credited nothing and the coach held "Spend so far is $0.56 of the $36 ...". The pair is still in the persisted
display transcript; the authority check reads it for a would-be hold.
"""
import importlib.util

import pytest
from test_compute_authority import RELAYED_ASK, call
from test_compute_envelope import HERE, _log

SPEND_LINE = 'Spend so far is $0.56 of the $36, and Vast shows exactly one GPU running.'


def _hooks_and_scope(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("coach_init_persisted", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("COACH_REPLY_RULES", "none")
    store = mod._load("core").Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, mod._load("goal_scope")


def _db():
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
    return SessionDB(get_hermes_home() / 'state.db')


def _summary(text):
    from agent.context_compressor import SUMMARY_PREFIX
    return f'{SUMMARY_PREFIX}\n{text}'


def _approved_then_compacted(session='authority', ask=RELAYED_ASK, reply='approved', rewind=False):
    """A priced ask, the user's bare yes, more work, then in-place compaction with no carried tail: the live window
    the hook sees no longer holds the pair. Returns that live window. `rewind`: the user took the yes back first."""
    db = _db()
    try:
        db.create_session(session, source='cli')
        db.append_message(session, 'user', 'run stage b, you may rent computed')
        db.append_message(session, 'assistant', ask)
        yes_id = db.append_message(session, 'user', reply)
        if rewind:
            db.rewind_to_message(session, yes_id)
        db.append_message(session, 'user', 'status?')
        db.append_message(session, 'assistant', 'The rerun card is unblocked and running.')
        db.archive_and_compact(session, [{'role': 'user', 'content': _summary('Earlier turns: GPU rerun work.')}],
                               tail_count=0)
        return db.get_messages_as_conversation(session)
    finally:
        db.close()


def _turn(hooks, live, user='\u2714 [default] Kanban t_1 done'):
    hooks['pre_llm_call'](session_id='authority', user_message=user, conversation_history=[
        *live, {'role': 'user', 'content': user}])


@pytest.mark.parametrize('surface', ['reply', 'kanban_create', 'kanban_block'])
def test_answered_ask_survives_compaction(tmp_path, monkeypatch, surface):
    hooks, _ = _hooks_and_scope(tmp_path, monkeypatch)
    live = _approved_then_compacted()
    assert not any(r.get('content') == 'approved' for r in live)  # the precondition of the live false hold
    _turn(hooks, live)
    assert call(hooks, surface, SPEND_LINE) is None
    # The persisted yes credits only the asked figure.
    assert call(hooks, surface, 'I will rent one L40S with a $100 cap.')
    reads = [r for r in _log(tmp_path) if r.get('event') == 'authority_persisted_read']
    assert reads and reads[0]['ok'] and reads[0]['usd'] == [36.0]


def test_rewound_yes_is_not_resurrected(tmp_path, monkeypatch):
    hooks, _ = _hooks_and_scope(tmp_path, monkeypatch)
    _turn(hooks, _approved_then_compacted(rewind=True))
    assert call(hooks, 'reply', SPEND_LINE)


def test_unpriced_ask_answered_before_compaction_credits_nothing(tmp_path, monkeypatch):
    hooks, _ = _hooks_and_scope(tmp_path, monkeypatch)
    _turn(hooks, _approved_then_compacted(ask='Should I keep monitoring the GPU rerun?'))
    assert call(hooks, 'reply', SPEND_LINE)


@pytest.mark.parametrize('history', ['live', 'persisted'])
def test_a_compaction_summary_is_not_the_answered_ask(tmp_path, monkeypatch, history):
    """A summary paraphrases older asks; a yes typed after it answers nothing in it."""
    hooks, _ = _hooks_and_scope(tmp_path, monkeypatch)
    summary = {'role': 'assistant', 'content': _summary(RELAYED_ASK)}
    if history == 'live':
        hooks['pre_llm_call'](session_id='authority', user_message='approved',
                              conversation_history=[summary, {'role': 'user', 'content': 'approved'}])
    else:
        db = _db()
        try:
            db.create_session('authority', source='cli')
            db.append_message('authority', 'assistant', summary['content'])
            db.append_message('authority', 'user', 'approved')
            db.archive_and_compact('authority', [{'role': 'user', 'content': _summary('later')}], tail_count=0)
            live = db.get_messages_as_conversation('authority')
        finally:
            db.close()
        _turn(hooks, live)
    assert call(hooks, 'reply', SPEND_LINE)


def test_persisted_transcript_is_read_only_for_a_would_be_hold(tmp_path, monkeypatch):
    hooks, scope = _hooks_and_scope(tmp_path, monkeypatch)
    live = _approved_then_compacted()
    reads = []
    real = scope.display_transcript
    monkeypatch.setattr(scope, 'display_transcript', lambda sid, *a, **k: reads.append(sid) or real(sid, *a, **k))
    _turn(hooks, live)
    for text in ('The rerun is still copying files to the GPU machine.', 'I will rent one GPU with a $20 cap.',
                 'GPU rental: spent USD 0.56 so far.'):
        assert call(hooks, 'reply', text) is None
    assert reads == []
    assert call(hooks, 'reply', SPEND_LINE) is None
    assert reads == ['authority']


def test_failed_persisted_read_credits_nothing_and_is_logged(tmp_path, monkeypatch):
    hooks, scope = _hooks_and_scope(tmp_path, monkeypatch)
    live = _approved_then_compacted()

    def boom(*_a, **_k):
        raise OSError('database is locked')
    monkeypatch.setattr(scope, 'display_transcript', boom)
    _turn(hooks, live)
    assert call(hooks, 'reply', SPEND_LINE)
    assert any(r.get('event') == 'authority_persisted_read' and r.get('ok') is False
               and 'database is locked' in r.get('error', '') for r in _log(tmp_path))


def test_display_transcript_is_empty_when_unknown(tmp_path, monkeypatch):
    _, scope = _hooks_and_scope(tmp_path, monkeypatch)
    assert scope.display_transcript('nope') == []
    _approved_then_compacted()
    assert any(r.get('content') == 'approved' for r in scope.display_transcript('authority'))
    assert scope.display_transcript('authority', max_rows=2) == []
    db = _db()
    try:
        db.create_session('elsewhere', source='cli', profile_name='other-profile')
        db.append_message('elsewhere', 'assistant', RELAYED_ASK)
        db.append_message('elsewhere', 'user', 'approved')
    finally:
        db.close()
    assert scope.display_transcript('elsewhere') == []

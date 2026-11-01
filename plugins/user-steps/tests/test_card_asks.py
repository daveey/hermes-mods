"""A reply relaying a blocked Kanban card's own ask publishes no second For-you request.

Replays a live duplicate (sanitized): a chat-closeout turn told a chat to relay a blocked card's ask word for
word, and the extractor minted an unlinked tile carrying the same two choices as the card. Whichever one the
user answered, the other went stale. Blocked cards with a human_request that are linked to the chat (or named
by the turn's runtime message) are now open asks too; a step restating one publishes nothing and the ledger
records ``restates_card_ask``. Every board, store and ledger lives under pytest's tmp_path.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}
CARD = 't_0123456789abcdef'
ACTION = ("Pick 'Turn it back on' or 'Keep it off' for the kitchen speaker microphone. It has been muted since "
          "Tuesday after someone told it to stop listening. Unblock = the agent turns the microphone back on; "
          "archive = it stays off.")
CHOICES = [{'label': 'Turn it back on', 'value': 'on'}, {'label': 'Keep it off', 'value': 'off'}]
VERBATIM = "Pick 'Turn it back on' or 'Keep it off' for the kitchen speaker microphone."
CLOSEOUT = (f"[chat-closeout] Work this chat started is now waiting on the user. Waiting on the user:\n"
            f"- Kitchen speaker mic is off: keep it off or turn it back on? (card other/{CARD}, blocked/needs_input)")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_board(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, human_request TEXT);"
                    "CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT);")
    c.commit()
    c.close()
    return path


def add_card(board: Path, tid=CARD, status='blocked', action=ACTION, subscriber='session-a'):
    c = sqlite3.connect(board)
    c.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?)",
              (tid, status, json.dumps({'action': action, 'choices': CHOICES}) if action else None))
    if subscriber:
        c.execute("INSERT INTO kanban_notify_subs VALUES (?,?,?)", (tid, 'tui', subscriber))
    c.commit()
    c.close()


def set_status(board: Path, status, tid=CARD):
    c = sqlite3.connect(board)
    c.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
    c.commit()
    c.close()


@pytest.fixture
def boards(tmp_path):
    root = tmp_path / 'kanban-root'
    return {'root': root, 'default': make_board(root / 'kanban.db'),
            'other': make_board(root / 'kanban' / 'boards' / 'other' / 'kanban.db'),
            'archived': make_board(root / 'kanban' / 'boards' / '_archived' / 'kanban.db')}


@pytest.fixture
def plugin(tmp_path, monkeypatch, boards):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    mod = load_module('card_asks_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(mod.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(mod.core, 'kanban_root', lambda: boards['root'])
    monkeypatch.setattr(mod.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(mod, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(mod, '_framed', lambda *a: True)
    monkeypatch.setattr(mod.core, '_worth_asking', lambda *a: True)
    return mod


def live(plugin):
    return [r for r in plugin.core.ActionStore().requests(OWNER) if r['state'] in ('open', 'needs_context')]


def ledger_card_asks(plugin):
    return plugin.core.Ledger().read('session-a').get('card_asks', [])


def turn(plugin, monkeypatch, user_message, reply, steps, repeats=None, seen=None, history=None):
    """One real turn: pre_llm_call (records the turn's message), then the extraction hook on the final reply."""
    payload = {'steps': steps, 'resolved': [], **({'repeats': repeats} if repeats is not None else {})}

    def llm(system, user):
        if seen is not None:
            seen.append(user)
        return json.dumps(payload)
    monkeypatch.setattr(plugin, 'aux_llm', llm)
    plugin.pre_llm_call(session_id='session-a', user_message=user_message, platform='cli',
                        conversation_history=history)
    return plugin.transform_llm_output(response_text=reply, session_id='session-a', platform='cli')


# ---- the open-asks list ------------------------------------------------------------------------------------

def test_lists_linked_and_named_blocked_cards_on_every_live_board_only(plugin, boards):
    add_card(boards['default'])                                                 # linked, blocked
    add_card(boards['other'], tid='t_1111111111111111', subscriber=None)      # named in the notice only
    add_card(boards['other'], tid='t_2222222222222222', subscriber=None)      # neither linked nor named
    add_card(boards['default'], tid='t_3333333333333333', status='ready')     # linked, not blocked
    add_card(boards['default'], tid='t_4444444444444444', action=None)       # linked, no human_request
    add_card(boards['archived'], tid='t_5555555555555555')                    # retired board copy
    notice = '[chat-closeout] Waiting on the user: (card other/t_1111111111111111, blocked/needs_input)'
    got = plugin.core.kanban_asks('session-a', notice)
    assert sorted(e['id'] for e in got) == ['kanban:default:' + CARD, 'kanban:other:t_1111111111111111']
    assert all(e['kind'] == 'kanban_ask' and e['text'] == ACTION for e in got)
    # A card id the user typed is no runtime notice: it does not add the card.
    assert [e['id'] for e in plugin.core.kanban_asks('session-a', 'what about t_1111111111111111?')] == \
        ['kanban:default:' + CARD]


# ---- extraction hook (transform_llm_output -> process_reply) ------------------------------------------------

def test_verbatim_relay_of_a_linked_blocked_cards_ask_publishes_no_request(plugin, monkeypatch, boards):
    add_card(boards['default'])
    seen = []
    reply = f"**What to do:** {VERBATIM} Reply in For you."
    step = {'text': 'Turn the kitchen speaker mic back on or keep it off', 'kind': 'choose',
            'verbatim_span': VERBATIM, 'options': ['Turn it back on', 'Keep it off']}
    out = turn(plugin, monkeypatch, CLOSEOUT, reply, [step], seen=seen)
    assert live(plugin) == []
    assert out is None  # nothing framed: the card is the request
    assert f'kanban:default:{CARD} (kanban_ask)' in seen[0]  # the model sees the card's ask as an open one
    rec = ledger_card_asks(plugin)
    assert [(r['verdict'], r['card'], r['board']) for r in rec] == [('restates_card_ask', CARD, 'default')]


def test_paraphrased_relay_publishes_no_request(plugin, monkeypatch, boards):
    add_card(boards['default'], subscriber='session-a')
    span = 'The kitchen speaker microphone is still muted: should the agent turn it back on, or keep it off?'
    step = {'text': 'Decide whether to turn the kitchen speaker microphone back on or keep it off', 'kind': 'input',
            'verbatim_span': span, 'options': []}
    turn(plugin, monkeypatch, 'any news?', span, [step])
    assert live(plugin) == []
    assert [r['card'] for r in ledger_card_asks(plugin)] == [CARD]


def test_model_marked_repeat_of_the_card_publishes_no_request(plugin, monkeypatch, boards):
    add_card(boards['default'])
    span = 'That one is still yours to call, whenever you get to it.'
    step = {'text': 'Make the call', 'kind': 'input', 'verbatim_span': span, 'options': []}
    turn(plugin, monkeypatch, 'status?', span, [step], repeats=[{'id': f'kanban:default:{CARD}', 'verbatim_span': span}])
    assert live(plugin) == []


def test_a_different_new_ask_in_the_same_reply_is_still_published(plugin, monkeypatch, boards):
    add_card(boards['default'])
    other = 'Want me to deploy build 412 to the test environment?'
    reply = f"{VERBATIM}\n\n{other}"
    steps = [{'text': 'Turn the kitchen speaker mic back on or keep it off', 'kind': 'choose',
              'verbatim_span': VERBATIM, 'options': ['Turn it back on', 'Keep it off']},
             {'text': 'Approve deploying build 412 to the test environment', 'kind': 'approve',
              'verbatim_span': other, 'options': []}]
    out = turn(plugin, monkeypatch, CLOSEOUT, reply, steps)
    assert [r['title'] for r in live(plugin)] == ['Approve deploying build 412 to the test environment']
    assert out and '::user-step' in out  # the new ask is framed as before
    assert [r['card'] for r in ledger_card_asks(plugin)] == [CARD]


@pytest.mark.parametrize('status', ['ready', 'archived', 'done'])
def test_card_unblocked_or_archived_leaves_the_list_and_a_later_reask_publishes(plugin, monkeypatch, boards, status):
    add_card(boards['default'])
    step = {'text': 'Turn the kitchen speaker mic back on or keep it off', 'kind': 'choose',
            'verbatim_span': VERBATIM, 'options': ['Turn it back on', 'Keep it off']}
    turn(plugin, monkeypatch, CLOSEOUT, VERBATIM, [step])
    assert live(plugin) == []
    set_status(boards['default'], status)
    assert plugin.core.kanban_asks('session-a', CLOSEOUT) == []
    turn(plugin, monkeypatch, 'is the mic still off? ask me again', VERBATIM, [step])
    assert [r['title'] for r in live(plugin)] == ['Turn the kitchen speaker mic back on or keep it off']


@pytest.mark.parametrize('labeled', [False, True], ids=['bare-row', 'kanban_notice-row'])
def test_card_named_only_in_the_chat_closeout_message_is_still_matched(plugin, monkeypatch, boards, labeled):
    add_card(boards['other'], subscriber=None)  # on another board, not linked to this chat
    step = {'text': 'Turn the kitchen speaker mic back on or keep it off', 'kind': 'choose',
            'verbatim_span': VERBATIM, 'options': ['Turn it back on', 'Keep it off']}
    # Live rows carry display_kind=kanban_notice (the hook labels them automatic); a bare row must match too.
    history = [{'role': 'user', 'content': CLOSEOUT, 'display_kind': 'kanban_notice'}] if labeled else None
    turn(plugin, monkeypatch, CLOSEOUT, VERBATIM, [step], history=history)
    assert live(plugin) == []
    assert [(r['card'], r['board']) for r in ledger_card_asks(plugin)] == [(CARD, 'other')]
    # Control: a turn the user started names no card, and the card is not linked here, so the ask is its own.
    turn(plugin, monkeypatch, 'tell me about the kitchen mic', VERBATIM, [step])
    assert len(live(plugin)) == 1


def test_steps_matching_no_card_behave_as_before(plugin, monkeypatch, boards):
    add_card(boards['default'])
    ask = 'Want me to delete the stale staging bucket?'
    step = {'text': 'Approve deleting the stale staging bucket', 'kind': 'approve', 'verbatim_span': ask,
            'options': []}
    turn(plugin, monkeypatch, 'clean up', ask, [step])
    assert [r['title'] for r in live(plugin)] == ['Approve deleting the stale staging bucket']
    assert ledger_card_asks(plugin) == []


def test_unreadable_board_fails_open(plugin, monkeypatch, boards):
    boards['default'].write_bytes(b'not a database')
    ask = 'Want me to delete the stale staging bucket?'
    step = {'text': 'Approve deleting the stale staging bucket', 'kind': 'approve', 'verbatim_span': ask,
            'options': []}
    turn(plugin, monkeypatch, 'clean up', ask, [step])
    assert len(live(plugin)) == 1


# ---- publication gate (pre_final_gate) ----------------------------------------------------------------------

APPROVE_ACTION = ("Approve renting one GPU machine for the speech model retrain, up to 20 dollars total. "
                  "Unblock = the agent rents it and runs the retrain; archive = no rental.")


def gate(plugin, monkeypatch, user_message, final, steps):
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': steps, 'resolved': []}))
    plugin.pre_llm_call(session_id='session-a', user_message=user_message, platform='cli')
    return plugin.publication_buffer(session_id='session-a', platform='cli', final_response=final)


def test_gate_does_not_hold_a_relay_of_a_linked_cards_approval_ask(plugin, monkeypatch, boards):
    add_card(boards['default'], action=APPROVE_ACTION)
    final = 'Approve renting one GPU machine for the speech model retrain, up to 20 dollars total?'
    step = {'text': 'Approve renting one GPU machine for the speech model retrain', 'kind': 'approve',
            'verbatim_span': final, 'options': []}
    assert gate(plugin, monkeypatch, CLOSEOUT, final, [step]) is None
    assert live(plugin) == []


def test_gate_does_not_hold_a_relay_of_a_card_named_only_in_the_runtime_message(plugin, monkeypatch, boards):
    add_card(boards['other'], action=APPROVE_ACTION, subscriber=None)
    final = 'Should I go ahead and rent the GPU machine for the speech model retrain (20 dollars cap)?'
    step = {'text': 'Approve renting the GPU machine for the speech model retrain', 'kind': 'approve',
            'verbatim_span': final, 'options': []}
    assert gate(plugin, monkeypatch, CLOSEOUT, final, [step]) is None


def test_gate_still_holds_a_different_approval_beside_a_card_relay(plugin, monkeypatch, boards):
    add_card(boards['default'], action=APPROVE_ACTION)
    relay = 'Approve renting one GPU machine for the speech model retrain, up to 20 dollars total?'
    other = 'Want me to deploy build 9 to production?'
    steps = [{'text': 'Approve renting one GPU machine for the speech model retrain', 'kind': 'approve',
              'verbatim_span': relay, 'options': []},
             {'text': 'Approve deploying build 9 to production', 'kind': 'approve', 'verbatim_span': other,
              'options': []}]
    out = gate(plugin, monkeypatch, CLOSEOUT, f'{relay}\n\n{other}', steps)
    assert out and out.get('hold') is True


def test_gate_holds_the_same_approval_once_the_card_is_unblocked(plugin, monkeypatch, boards):
    add_card(boards['default'], action=APPROVE_ACTION)
    set_status(boards['default'], 'ready')
    final = 'Approve renting one GPU machine for the speech model retrain, up to 20 dollars total?'
    step = {'text': 'Approve renting one GPU machine for the speech model retrain', 'kind': 'approve',
            'verbatim_span': final, 'options': []}
    out = gate(plugin, monkeypatch, 'rent it?', final, [step])
    assert out and out.get('hold') is True

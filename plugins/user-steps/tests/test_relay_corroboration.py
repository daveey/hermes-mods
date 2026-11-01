"""Relays that name a blocked card by its topic, and model "repeats" the reply does not back up.

Replays two live failures (sanitized):

- A chat-closeout reply said "unblock the fseventsd card in Hermes (it's in For you), then approve Touch ID in the
  terminal window that opens." The card's own ask is "window closed 00:09 PDT. Unblock = agent reopens window;
  archive = drop request.", so the step shared 2 of 8 words with it and a second, unlinked For-you request was
  minted. A card ask entry now carries the card's title: a relay that tells the user to unblock/archive "the
  <topic> card" is the card's ask when the topic names exactly one listed card, and a compound "unblock X, then
  approve Y" is covered by its first action.
- The extractor listed four unrelated status paragraphs as "repeats" of the session's only open tile (the CuaDriver
  Accessibility step), and each rendered as that tile with its buttons. A repeat is now framed or mapped only when
  the sentence or its paragraph corroborates it; the ledger records ``repeat_uncorroborated`` otherwise.

Every board, store and ledger lives under pytest's tmp_path.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}

# ---- the fseventsd relay (live ledger step us_c1d2afd7e7, card t_01a195802e9077ac) ------------------------
FSE_CARD = 't_01a195802e9077ac'
FSE_TITLE = ('Laptop fseventsd at ~100% of a core since ~18:48 PDT 10-31 (2-7% on 10-28..30): '
             'find the FSEvents client and stop it')
FSE_ACTION = 'window closed 00:09 PDT. Unblock = agent reopens window; archive = drop request.'
FSE_SPAN = ("**What to do:** unblock the fseventsd card in Hermes (it's in For you), then approve Touch ID in the "
            "terminal window that opens.")
FSE_REPLY = FSE_SPAN + ' The card\'s ask: "window closed 00:09 PDT. Unblock = agent reopens window; archive = drop request."'
FSE_STEP = {'text': ('Unblock the fseventsd card and approve the Touch ID prompt in the terminal window to let the '
                     'discriminating trace run'), 'kind': 'do', 'verbatim_span': FSE_SPAN, 'options': []}
CLOSEOUT = (f"[chat-closeout] Work this chat started is now waiting on the user. Waiting on the user:\n"
            f"- fseventsd trace needs a terminal window (card default/{FSE_CARD}, blocked/needs_input)")

KITCHEN_CARD = 't_0123456789abcdef'
KITCHEN_TITLE = 'Kitchen speaker microphone muted since Tuesday'
KITCHEN_ACTION = ("Pick 'Turn it back on' or 'Keep it off' for the kitchen speaker microphone. Unblock = the agent "
                  "turns the microphone back on; archive = it stays off.")

# ---- the CuaDriver tile and the four report paragraphs framed onto it (MacBook chat ..bef6c6) -------------
TILE_PARAGRAPH = ("**Still needs you (same ask as before):** on the MacBook, open System Settings → Privacy & Security "
                  "→ Accessibility and turn on CuaDriver. The latest reading (16:16) shows Accessibility off and "
                  "Screen Recording on. Once it's on, an agent can test the sidebar Archive button with a real click.")
TILE_STEP = {'text': 'Turn on CuaDriver in System Settings → Privacy & Security → Accessibility on the MacBook',
             'kind': 'do', 'verbatim_span': ('on the MacBook, open System Settings → Privacy & Security → '
                                             'Accessibility and turn on CuaDriver'), 'options': []}
PARAGRAPHS = [
    ("**The follow-up card (t_c21c170e) had stalled:**\n"
     "- Its next step is a design for reusing merge-train test results in release builds. Release builds are now the "
     "biggest cost at about 548 minutes a day.\n"
     "- It tried to hand that design to the hub's tuner profile, and card creation refused it. The MacBook checked "
     "tuner against its own profiles, even though the card would run on the hub. The card then parked behind an "
     "unrelated wait.\n"
     "- I filed its design card on the hub through a route that works (t_01a194e6dd31405b, its spec word for word). "
     "I also filed a fix for the bug (t_01a194e6f8b9b626)."),
    ("**Earlier mini disk cards:** neither one replaces H5.\n"
     "- **H3 (t_01a18329f9d7029d, done 10-28)** was the earlier round. It capped Spotlight, fixed the disk-guard hang "
     "and the crashing worktree cleanup, and the guard was reinstalled on the mini from main on 10-30 at 01:50. Space "
     "ran low again after that because of new writers H5 found later: macOS 27.2 updates downloading into other disk "
     "volumes, and the Swift build cache.\n"
     "- **The owner-wake card (t_01a18c1bc899ff93, done 10-30)** only confirmed H5 and H6 were still running after "
     "their chat closed. It changed nothing."),
    ("- **A buried ask:** a 10-27 planning doc in the app repo asks you and your partner for a deliberate "
     "voice-sample session. It was never put on a card, so it never reached you."),
    ("I removed a duplicate copy of the Living Room Echo Dot question from your For you list. My last message had "
     "accidentally created it."),
]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_board(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, human_request TEXT);"
                    "CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT);")
    c.commit()
    c.close()
    return path


def add_card(board: Path, tid, title, action, subscriber='session-a', choices=()):
    c = sqlite3.connect(board)
    c.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?)",
              (tid, title, 'blocked', json.dumps({'action': action, 'choices': list(choices)})))
    if subscriber:
        c.execute("INSERT INTO kanban_notify_subs VALUES (?,?,?)", (tid, 'tui', subscriber))
    c.commit()
    c.close()


@pytest.fixture
def board(tmp_path):
    return make_board(tmp_path / 'kanban-root' / 'kanban.db')


@pytest.fixture
def plugin(tmp_path, monkeypatch, board):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    mod = load_module('relay_corroboration_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(mod.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(mod.core, 'kanban_root', lambda: tmp_path / 'kanban-root')
    monkeypatch.setattr(mod.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(mod, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(mod, '_framed', lambda *a: True)
    monkeypatch.setattr(mod.core, '_worth_asking', lambda *a: True)
    return mod


def live(plugin):
    return [r for r in plugin.core.ActionStore().requests(OWNER) if r['state'] in ('open', 'needs_context')]


def ledger(plugin):
    return plugin.core.Ledger().read('session-a')


def turn(plugin, monkeypatch, user_message, reply, steps, repeats=None, seen=None):
    """One real turn through the extraction hook: pre_llm_call, then transform_llm_output on the final reply."""
    payload = {'steps': steps, 'resolved': [], **({'repeats': repeats} if repeats is not None else {})}

    def llm(system, user):
        if seen is not None:
            seen.append(user)
        return json.dumps(payload)
    monkeypatch.setattr(plugin, 'aux_llm', llm)
    plugin.pre_llm_call(session_id='session-a', user_message=user_message, platform='cli')
    return plugin.transform_llm_output(response_text=reply, session_id='session-a', platform='cli')


def gate(plugin, monkeypatch, user_message, final, steps, repeats=None):
    payload = {'steps': steps, 'resolved': [], **({'repeats': repeats} if repeats is not None else {})}
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps(payload))
    plugin.pre_llm_call(session_id='session-a', user_message=user_message, platform='cli')
    return plugin.publication_buffer(session_id='session-a', platform='cli', final_response=final)


# ---- A. a relay naming the card by its topic ------------------------------------------------------------

def test_real_fseventsd_relay_publishes_no_request_and_is_recorded(plugin, monkeypatch, board):
    add_card(board, FSE_CARD, FSE_TITLE, FSE_ACTION)
    seen = []
    out = turn(plugin, monkeypatch, CLOSEOUT, FSE_REPLY, [FSE_STEP], seen=seen)
    assert live(plugin) == []
    assert out is None  # nothing framed: the card is the request
    rec = ledger(plugin).get('card_asks', [])
    assert [(r['verdict'], r['card'], r['board']) for r in rec] == [('restates_card_ask', FSE_CARD, 'default')]
    # The extractor sees the card's title next to its ask.
    assert f'card {FSE_CARD} "{FSE_TITLE}": ask: {FSE_ACTION}' in seen[0]


def test_real_fseventsd_relay_does_not_hold_the_publication_gate(plugin, monkeypatch, board):
    add_card(board, FSE_CARD, FSE_TITLE, FSE_ACTION)
    # The gate only judges approvals: the same relay read as an approval must not demand a second request.
    step = dict(FSE_STEP, kind='approve')
    assert gate(plugin, monkeypatch, CLOSEOUT, FSE_REPLY, [step]) is None
    assert live(plugin) == []


def test_unblock_the_topic_card_maps_only_to_the_named_card(plugin, monkeypatch, board):
    add_card(board, FSE_CARD, FSE_TITLE, FSE_ACTION)
    add_card(board, KITCHEN_CARD, KITCHEN_TITLE, KITCHEN_ACTION)
    for topic, card in (('fseventsd', FSE_CARD), ('kitchen speaker', KITCHEN_CARD)):
        span = f'Unblock the {topic} card when you get a minute.'
        step = {'text': f'Unblock the {topic} card', 'kind': 'do', 'verbatim_span': span, 'options': []}
        entries = plugin.core.kanban_asks('session-a', CLOSEOUT)
        assert plugin.core.card_ask_for(step, entries)['task_id'] == card
        turn(plugin, monkeypatch, 'status?', span, [step])
        assert live(plugin) == []
        assert ledger(plugin)['card_asks'][-1]['card'] == card


def test_unblock_a_topic_that_matches_no_card_is_still_published(plugin, monkeypatch, board):
    add_card(board, FSE_CARD, FSE_TITLE, FSE_ACTION)
    add_card(board, KITCHEN_CARD, KITCHEN_TITLE, KITCHEN_ACTION)
    span = 'Unblock the printer firmware card when you get a minute.'
    step = {'text': 'Unblock the printer firmware card', 'kind': 'do', 'verbatim_span': span, 'options': []}
    turn(plugin, monkeypatch, 'status?', span, [step])
    assert [r['title'] for r in live(plugin)] == ['Unblock the printer firmware card']
    assert ledger(plugin).get('card_asks', []) == []


def test_unblock_topic_relay_does_not_hold_the_gate_and_an_unknown_topic_does(plugin, monkeypatch, board):
    add_card(board, FSE_CARD, FSE_TITLE, FSE_ACTION)
    add_card(board, KITCHEN_CARD, KITCHEN_TITLE, KITCHEN_ACTION)
    span = 'Unblock the kitchen speaker card so the agent can turn the mic back on?'
    step = {'text': 'Unblock the kitchen speaker card', 'kind': 'approve', 'verbatim_span': span, 'options': []}
    assert gate(plugin, monkeypatch, 'status?', span, [step]) is None
    span = 'Unblock the printer firmware card so the agent can flash it?'
    step = {'text': 'Unblock the printer firmware card', 'kind': 'approve', 'verbatim_span': span, 'options': []}
    out = gate(plugin, monkeypatch, 'status?', span, [step])
    assert out and out.get('hold') is True


def test_a_word_shared_by_two_cards_titles_names_neither(plugin, board):
    add_card(board, FSE_CARD, 'Laptop fseventsd trace', FSE_ACTION)
    add_card(board, KITCHEN_CARD, 'Router network trace', 'Approve the capture. Unblock = agent runs it; archive = no.')
    step = {'text': 'Unblock the trace card', 'kind': 'do', 'verbatim_span': 'Unblock the trace card.',
            'options': []}
    assert plugin.core.card_ask_for(step, plugin.core.kanban_asks('session-a')) is None


# ---- B. a model repeat must be corroborated -------------------------------------------------------------

core = load_module('relay_corroboration_core', ROOT / 'core.py')


def env(tmp_path):
    return core.ActionStore(tmp_path / 'actions.sqlite3'), core.Ledger(tmp_path / 'ledger', tmp_path / 'missing.db')


def reply(store, lg, text, steps, repeats=None, user_msg=''):
    payload = {'steps': steps, 'resolved': [], **({'repeats': repeats} if repeats is not None else {})}
    return core.process_reply(text, 'session-a', ledger=lg, action_store=store, user_msg=user_msg,
                              action_owner=OWNER, llm=lambda *a: json.dumps(payload))


def open_tile(tmp_path):
    store, lg = env(tmp_path)
    reply(store, lg, TILE_PARAGRAPH, [TILE_STEP])
    [tile] = lg.open_steps('session-a')
    assert tile.get('requestId')
    return store, lg, tile


@pytest.mark.parametrize('paragraph', PARAGRAPHS, ids=['stalled-card', 'disk-cards', 'buried-ask', 'removed-dup'])
def test_unrelated_report_paragraph_named_as_a_repeat_is_not_framed_or_mapped(tmp_path, paragraph):
    store, lg, tile = open_tile(tmp_path)
    text = f"Status as of now.\n\n{paragraph}\n\nNothing else changed."
    # The model names the tile for every sentence of the paragraph: none of them is framed onto it.
    spans = [ln.strip('- ').strip() for ln in paragraph.split('\n')]
    out = reply(store, lg, text, [], repeats=[{'id': tile['id'], 'verbatim_span': s} for s in spans])
    assert out['text'] is None or '::user-step' not in out['text']
    assert out['steps'] == []
    checks = lg.read('session-a').get('repeat_checks', [])
    assert {c['verdict'] for c in checks} == {'repeat_uncorroborated'} and {c['tile'] for c in checks} == {tile['id']}
    # Not mapped either: the identity matcher and the detector refuse the model's id for it.
    asks = core.open_asks(store, OWNER, lg.read('session-a')['steps'])
    for s in spans:
        candidate = {'text': s[:120], 'kind': 'do', 'verbatim_span': s, 'options': []}
        rep = [{'id': tile['id'], 'verbatim_span': s}]
        assert core.same_open_ask(candidate, asks, rep, reply=text) is None
        assert core.restates_open_ask(candidate, asks, rep, reply=text) is None
    assert len([r for r in store.requests(OWNER) if r['state'] == 'open']) == 1


def test_unrelated_paragraph_the_model_also_lists_as_a_step_is_not_folded_onto_the_tile(tmp_path):
    store, lg, tile = open_tile(tmp_path)
    span = ('- **A buried ask:** a 10-27 planning doc in the app repo asks you and your partner for a deliberate '
            'voice-sample session.')
    step = {'text': 'Do a deliberate voice-sample session', 'kind': 'do', 'verbatim_span': span, 'options': []}
    out = reply(store, lg, PARAGRAPHS[2], [step], repeats=[{'id': tile['id'], 'verbatim_span': span}])
    # Judged by the other rules: it is its own ask, never the CuaDriver tile.
    assert [s['id'] for s in out['steps']] != [tile['id']]
    assert tile['id'] not in (out['text'] or '')


def test_a_genuine_repeat_quoting_the_tile_is_still_framed(tmp_path):
    store, lg, tile = open_tile(tmp_path)
    span = ('Still waiting on one thing: on the MacBook, open System Settings → Privacy & Security → Accessibility '
            'and turn on CuaDriver.')
    out = reply(store, lg, f"Status as of now.\n\n{span}", [], repeats=[{'id': tile['id'], 'verbatim_span': span}])
    assert out['text'] and f'id="{tile["id"]}"' in out['text']
    assert lg.read('session-a').get('repeat_checks', []) == []


def test_a_genuine_reworded_repeat_sharing_the_tiles_words_is_still_framed(tmp_path):
    store, lg, tile = open_tile(tmp_path)
    span = 'Your request to turn on CuaDriver under Accessibility is still current.'
    out = reply(store, lg, span, [], repeats=[{'id': tile['id'], 'verbatim_span': span}])
    assert out['text'] and f'id="{tile["id"]}"' in out['text']


def test_uncorroborated_repeat_of_a_card_ask_maps_nothing(tmp_path):
    entry = {'id': f'kanban:default:{FSE_CARD}', 'kind': 'kanban_ask', 'text': FSE_ACTION, 'board': 'default',
             'task_id': FSE_CARD, 'labels': [], 'title': FSE_TITLE}
    span = PARAGRAPHS[3].split('. ')[0] + '.'
    candidate = {'text': 'Check the For you list', 'kind': 'do', 'verbatim_span': span, 'options': []}
    assert core.card_ask_for(candidate, [entry], [{'id': entry['id'], 'verbatim_span': span}], PARAGRAPHS[3]) is None
    # The same repeat naming the card's id is corroborated.
    span = f'Card {FSE_CARD} is still yours to call.'
    candidate = dict(candidate, verbatim_span=span)
    assert core.card_ask_for(candidate, [entry], [{'id': entry['id'], 'verbatim_span': span}], span) is entry

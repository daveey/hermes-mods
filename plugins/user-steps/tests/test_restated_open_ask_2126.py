"""A reply restating an open ask (MacBook chat ..bef6c6, sanitized): one open For-you request, then a reply whose
fourth status bullet restated that question, and a later section that quoted another card's ask line and said
"Reply here." Live, the reply minted a second request with no pre-send hold, the new request's scope was the
header plus all four bullets, and "Reply here." was booked as a restatement of an unrelated card.

Decided behavior:
(a) While the chat has an open request, a final reply that yields an ask nothing maps is held once by the
    pre-send gate, in a fresh, resumed or Kanban-notice turn. The gate and the transform read one extraction, so
    a tile is never minted beside an open one unless the gate held first; a re-sent reply follows the old rules.
(b) A minted request's scope and the step's paragraph are the text frame() put in the directive's ``t``.
(c) A sentence restates card X's ask only when its paragraph names X or quotes X's ask line (or its section
    quotes X's ask line); text in a section quoting another card's ask line is never booked to X.
Every placeholder (card ids, PR numbers, URLs, names) is invented. Stores and ledgers live under tmp_path.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}

OPEN_ASK = ("Reply A or B here. If you don't, the card stays blocked and nothing ships. The half-built change "
            "stays saved on worker-9.")
OPEN_STEP = {'text': "Reply A or B to the stalled card's proof choice (live window vs. old replay)", 'kind': 'choose',
             'verbatim_span': "Reply A or B here. If you don't, the card stays blocked and nothing ships.",
             'options': ['A: live window', 'B: old replay']}

PAD = ("The review approved the exact version of the change, the required checks went green after one rerun of "
       "an unrelated flaky test whose fix is already on main, and the owning card is landing it now; nothing here "
       "needs the user. ")
RESTATED = "One answer, in either place, covers both."
SAME_Q = "it's the same question I asked at 8:45."
BULLET4 = (f"- **Stalled-card proof question:** {SAME_Q} The other chat that owns that card asked you too, with one "
           f"added condition. {RESTATED}")
UPDATES = ("**The four updates:**\n"
           f"- **PR-1 (example inventory):** {PAD}https://example.invalid/runs/1\n"
           f"- **#1001 (example quota clear):** {PAD}https://example.invalid/runs/2\n"
           f"- **#1002 (example command guard):** {PAD}https://example.invalid/runs/3\n"
           f"{BULLET4}")
QUOTED_ASK = ("Choose whether to accept the bounded investigation and waive the old replay, keeping live proof "
              "required. Unblock = resume under your selected acceptance; archive = stop without claiming it passed.")
REPLY = ("The quota fix is installed on the hub and most workers, and it works live.\n\n"
         f"{UPDATES}\n\n"
         "**One decision for you: how to prove the landing fix works**\n"
         "Two of its acceptance checks need history that was never recorded.\n\n"
         f"> {QUOTED_ASK}\n\n"
         "Reply here. My pick: accept the investigation, waive the replay, keep live proof.\n")
LOCAL_CARD = 't_0e0e0e0e'  # a blocked local card the reply never names
LOCAL_ASK = ("Reply here with yes to submit the example app build to the store review queue, or keep it local. "
             "Unblock = submit; archive = keep it local.")  # shares "reply", "here", "keep" with "Reply here. ..."


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load_module('restated_open_ask_core', ROOT / 'core.py')


def card_entry(tid=LOCAL_CARD, action=LOCAL_ASK, title='Example app store submission'):
    return {'id': f'kanban:default:{tid}', 'kind': core.KANBAN_ASK, 'text': action, 'board': 'default',
            'task_id': tid, 'labels': [], 'title': title}


def payload(steps=(), repeats=()):
    return json.dumps({'steps': list(steps), 'resolved': [], 'repeats': list(repeats)})


def new_step():
    return {'text': "Pick A or B for the stalled card's proof choice (live window vs. old replay)", 'kind': 'choose',
            'verbatim_span': RESTATED, 'options': ['Use live proof', 'Keep the old replay']}


def repeat_of(open_ids, span=SAME_Q):
    # The extractor names the open ask by whichever id the prompt listed it under (ledger step or request id);
    # extract() keeps only listed ids.
    return [{'id': i, 'verbatim_span': span} for i in open_ids]


# ---- (b) scope = the directive's text ---------------------------------------------------------------------

def env(tmp_path):
    return core.ActionStore(tmp_path / 'actions.sqlite3'), core.Ledger(tmp_path / 'ledger', tmp_path / 'missing.db')


def open_first_ask(store, ledger):
    out = core.process_reply(OPEN_ASK, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             llm=lambda s, u: payload([OPEN_STEP]))
    (step,) = out['steps']
    return step


def decode_t(framed):
    import re
    m = re.search(r'::user-step\{id="([^"]+)" s="[^"]*" t="((?:[^"\\]|\\.)*)"\}', framed)
    return m.group(1).split(','), core.dec(m.group(2))


def test_fixture_block_is_over_the_attr_budget():
    block = core.locate(REPLY, RESTATED)
    assert block['text'] == UPDATES and len(core.enc(UPDATES)) > core.ATTR_BUDGET


def test_minted_scope_is_the_framed_line_never_the_whole_list(tmp_path):
    store, ledger = env(tmp_path)
    first = open_first_ask(store, ledger)
    out = core.process_reply(REPLY, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             llm=lambda s, u: payload([new_step()], repeat_of([first['id'], first['requestId']])))
    minted = [s for s in out['steps'] if s['id'] != first['id']]
    assert len(minted) == 1  # no gate in this path: the transform alone still mints the step
    ids, t = decode_t(out['text'])
    assert set(ids) == {minted[0]['id'], first['id']}  # one directive groups the new step and the open tile
    assert t == BULLET4
    assert minted[0]['paragraph'] == t
    request = store.get(OWNER, minted[0]['requestId'])
    assert request['scope']['markdown'] == t
    for unrelated in ('PR-1', '#1001', '#1002', 'The four updates'):
        assert unrelated not in request['scope']['markdown']


def test_paragraph_under_the_budget_is_still_the_whole_paragraph(tmp_path):
    store, ledger = env(tmp_path)
    text = "**Next:**\n- The deploy is done.\n- Want me to restart the example service now?"
    out = core.process_reply(text, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             llm=lambda s, u: payload([{'text': 'Approve restarting the example service',
                                                        'kind': 'approve', 'options': [],
                                                        'verbatim_span': 'Want me to restart the example service now?'}]))
    (step,) = out['steps']
    _, t = decode_t(out['text'])
    assert t == step['paragraph'] == text


def test_framed_text_matches_frame_for_a_grouped_directive():
    # The mint decides its text before the block's id group is final; reserve makes it pick what frame() picks.
    steps = [{'id': 'us_aaaaaaaaaa', 'verbatim_span': RESTATED}, {'id': 'us_bbbbbbbbbb', 'verbatim_span': SAME_Q}]
    _, t = decode_t(core.frame(REPLY, steps, 'session-a'))
    assert core.framed_text(REPLY, RESTATED, 'session-a', reserve=2) == t


# ---- (c) card-ask booking needs a citation -----------------------------------------------------------------

def test_reply_here_under_another_cards_quoted_ask_is_not_booked_to_an_unnamed_card(tmp_path):
    store, ledger = env(tmp_path)
    entry = card_entry()
    out = core.process_reply(REPLY, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             card_asks=[entry], llm=lambda s, u: payload([], [{'id': entry['id'],
                                                                              'verbatim_span': 'Reply here.'}]))
    assert 'card_asks' not in out
    assert ledger.read('session-a').get('card_asks', []) == []


def test_a_step_in_a_section_quoting_another_cards_ask_is_never_that_unnamed_card(tmp_path):
    entry = card_entry()
    step = {'text': 'Reply here', 'kind': 'choose', 'verbatim_span': 'Reply here.', 'options': []}
    assert core.card_ask_for(step, [entry], [{'id': entry['id'], 'verbatim_span': 'Reply here.'}], REPLY) is None
    assert core.cited_cards('Reply here.', REPLY, [entry]) == []


def test_quoting_or_naming_the_card_still_books_the_restatement(tmp_path):
    entry = card_entry(tid='t_0a0a0a0a', action=QUOTED_ASK, title='Landing fix acceptance')
    other = card_entry()
    rep = [{'id': entry['id'], 'verbatim_span': 'Reply here.'}]
    store, ledger = env(tmp_path)
    out = core.process_reply(REPLY, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             card_asks=[other, entry], llm=lambda s, u: payload([], rep))
    assert out.get('card_asks') == [entry['id']]
    named = f"Card {LOCAL_CARD} still waits: approve the store submission there."
    assert core.cited_cards(named, named, [other, entry]) == [other]  # naming the card cites it, and only it


# ---- (a) the gate holds what the transform would mint -------------------------------------------------------

FRESH = 'how are the four PRs doing?'
RESUMED = ('[System note: Your previous turn was interrupted by an update restart. Continue the task from where '
           'you left off.]')
NOTICE = ('\u23f8 [hub:default] @default Kanban t_0a0a0a0a blocked \u2014 actor: agent (task t_0a0a0a0a)\n'
          'Next action for the user: ' + QUOTED_ASK)


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    mod = load_module('restated_open_ask_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(mod.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(mod.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(mod.core, 'kanban_asks', lambda *a, **k: [])
    monkeypatch.setattr(mod, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(mod, '_framed', lambda *a: True)
    return mod


def open_requests(plugin):
    return [r for r in plugin.core.ActionStore().requests(OWNER)
            if r['state'] in ('open', 'needs_context') and not r.get('resolution')]


def first_turn(plugin, monkeypatch):
    monkeypatch.setattr(plugin, 'aux_llm', lambda s, u: payload([OPEN_STEP]))
    plugin.pre_llm_call(session_id='session-a', user_message='status?', platform='cli')
    plugin.transform_llm_output(response_text=OPEN_ASK, session_id='session-a', platform='cli')
    (req,) = open_requests(plugin)
    (step,) = plugin.core.Ledger().open_steps('session-a')
    return [step['id'], req['requestId']]


def sequenced(outputs):
    calls = []

    def llm(system, user):
        calls.append(user)
        return outputs[min(len(calls), len(outputs)) - 1]
    return llm, calls


def deliver(plugin, user_message):
    """One turn as core runs it: pre_llm_call, the pre-send gate (re-sent once after a hold, like core's loop with
    the nudge in the turn), then the transform on the delivered reply. Returns the gate's first verdict."""
    plugin.pre_llm_call(session_id='session-a', user_message=user_message, platform='cli')
    first = plugin.publication_buffer(session_id='session-a', platform='cli', final_response=REPLY, attempt=0,
                                      hold_budget=2, turn_messages=[])
    if first and first.get('hold'):
        nudged = [{'role': 'user', 'content': first['nudge'], '_pre_final_gate_synthetic': True}]
        again = plugin.publication_buffer(session_id='session-a', platform='cli', final_response=REPLY, attempt=1,
                                          hold_budget=2, turn_messages=nudged)
        assert not (again and again.get('hold')), 'the open-request check holds once per turn'
    plugin.transform_llm_output(response_text=REPLY, session_id='session-a', platform='cli')
    return first


@pytest.mark.parametrize('user_message', [FRESH, RESUMED, NOTICE], ids=['fresh', 'resumed', 'kanban-notice'])
def test_restated_ask_with_a_new_step_is_held_once_in_every_turn_kind(plugin, monkeypatch, user_message):
    open_ids = first_turn(plugin, monkeypatch)
    monkeypatch.setattr(plugin, 'aux_llm', lambda s, u: payload([new_step()], repeat_of(open_ids)))
    first = deliver(plugin, user_message)
    assert first and first.get('hold') is True and plugin.OPEN_ASK_NUDGE_HEAD in first['nudge']
    assert RESTATED in first['nudge']


@pytest.mark.parametrize('order', ['gate-reads-repeat-only', 'gate-reads-new-step'])
@pytest.mark.parametrize('user_message', [FRESH, RESUMED, NOTICE], ids=['fresh', 'resumed', 'kanban-notice'])
def test_no_tile_is_minted_beside_an_open_one_unless_the_gate_held(plugin, monkeypatch, user_message, order):
    """The live failure: the gate's own extractor call read the restated bullet as a repeat only (no hold) while
    the transform's call read a new step there and minted it. Whatever the model answers, the two now agree."""
    open_ids = first_turn(plugin, monkeypatch)
    repeat_only, with_step = payload([], repeat_of(open_ids)), payload([new_step()], repeat_of(open_ids))
    outputs = [repeat_only, with_step] if order == 'gate-reads-repeat-only' else [with_step, repeat_only]
    llm, calls = sequenced(outputs)
    monkeypatch.setattr(plugin, 'aux_llm', llm)
    first = deliver(plugin, user_message)
    held = bool(first and first.get('hold'))
    minted = len(open_requests(plugin)) - 1
    assert minted == 0 or held, f'minted {minted} tile(s) beside the open request with no pre-send hold'
    assert len(calls) == 1  # one extraction for the reply: the gate's, reused by the re-send and the transform
    if held:
        assert minted == 1  # a re-sent reply follows the existing rules: the agent kept it, so it is its own tile

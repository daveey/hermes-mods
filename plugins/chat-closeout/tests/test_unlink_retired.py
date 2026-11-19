"""An entry the automatic unlink retired stays retired after its close-out.

Live case (t_01a1dfe070b89919): a linked remote card archived without completion is never republished, so the
replica keeps its last pre-withdrawal row (stale, status ``running``) forever. ``track_unlinked`` aged it out of
the batch after 24 h, the close-out turn named it, and that turn's end put it straight back: ``open_card_ids``
(core ``session_tasks``) still lists the stale row as open, so ``record_turn_end`` re-added it to the batch and a
new 24 h clock started, with another close-out wake a day later, forever.

Retired entries are remembered (ledger ``retired``) and kept out of the batch while nothing new is known about the
card: still stale/missing/unresolved, or an unreadable read. A fresh, resolvable row (the owner republished it) is
genuine work again and rejoins; any other new card a turn hands work to is tracked as before. Throwaway SQLite.
"""
import pytest
from test_closeout import plugin, core
from test_unlink import world, tick, inv, H  # noqa: F401  (fixture)

STRANDED = 'hub/default/runners/t_stranded'
LIVE = 'hub/default/build/t_live'


def stale(key, status='running'):
    return {**inv(key, status), 'stale': True}


@pytest.fixture
def stranded(world, monkeypatch):
    led, inventory, _ = world
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)   # hourly ticks in the test
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    inventory.extend([inv(LIVE, 'done'), stale(STRANDED)])
    opened = [STRANDED]                     # core keeps listing the stale row as an open card of the chat
    monkeypatch.setattr(plugin, 'open_card_ids', lambda _sid: list(opened))
    led.write('chat', batch=[STRANDED], open=[STRANDED])
    return led, inventory, opened


def turn_end():
    plugin.post_llm_call(session_id='chat', platform='desktop', conversation_history=[
        {'role': 'assistant', 'content': 'Closed out.', 'finish_reason': 'stop'}])


def unlink_and_close_out(led):
    calls = []
    for h in range(26):
        calls += tick(1000 + h * H)
    row = led.get('chat')
    assert STRANDED not in row['batch'] and 'removed' in row['unlinked'][STRANDED]
    assert len(calls) == 1 and f'{STRANDED}: no longer linked' in calls[0]
    assert row['closing'] is not None         # the waker started the close-out turn
    turn_end()
    return 1000 + 26 * H


def test_close_out_turn_end_does_not_relink_the_retired_entry(stranded):
    led, _, _ = stranded
    t = unlink_and_close_out(led)
    row = led.get('chat')
    assert STRANDED not in row['batch']
    assert STRANDED not in row['open']
    assert STRANDED not in row['unlinked']    # no new clock
    calls = []
    for h in range(1, 60):                     # well past another 24 h of healthy observation
        calls += tick(t + h * H)
        if h % 7 == 0:
            turn_end()                         # ordinary turns keep listing it too
    assert calls == []                         # no daily close-out wake for the same retired entry
    row = led.get('chat')
    assert STRANDED not in row['batch'] and STRANDED not in row['unlinked']


def test_new_work_after_the_close_out_is_still_tracked(stranded):
    led, inventory, opened = stranded
    t = unlink_and_close_out(led)
    nxt = 'hub/default/build/t_next'
    inventory.append(inv(nxt))
    opened.append(nxt)
    turn_end()
    assert led.get('chat')['batch'] == [nxt]
    inventory[-1] = inv(nxt, 'done')
    calls = tick(t + H)
    assert len(calls) == 1 and nxt in calls[0] and STRANDED not in calls[0]


def test_republished_card_is_work_again(stranded):
    led, inventory, _ = stranded
    t = unlink_and_close_out(led)
    inventory[1] = inv(STRANDED)            # the owner publishes it again, fresh and running
    turn_end()
    row = led.get('chat')
    assert STRANDED in row['batch'] and STRANDED in row['open']
    assert STRANDED not in (row.get('retired') or {})
    inventory[1] = inv(STRANDED, 'done')
    calls = tick(t + H)
    assert len(calls) == 1 and STRANDED in calls[0]


def test_unreadable_read_keeps_the_entry_retired(stranded, monkeypatch):
    led, _, _ = stranded
    unlink_and_close_out(led)
    monkeypatch.setattr(plugin, 'remote_cards', lambda: (_ for _ in ()).throw(OSError('replica down')))
    turn_end()
    row = led.get('chat')
    assert STRANDED not in row['batch'] and STRANDED in row['retired']


def test_failed_close_out_turn_retires_nothing(stranded):
    led, _, _ = stranded
    for h in range(26):
        tick(1000 + h * H)
    plugin.post_llm_call(session_id='chat', platform='desktop', conversation_history=[
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'x'}]}])
    row = led.get('chat')
    # not delivered: the removed mention is retried, and the entry is not retired yet
    assert 'removed' in row['unlinked'][STRANDED] and not (row.get('retired') or {})


def test_tombstone_dropped_once_the_chat_stops_listing_the_card(stranded):
    led, _, opened = stranded
    unlink_and_close_out(led)
    assert STRANDED in led.get('chat')['retired']
    opened.clear()
    turn_end()
    assert not led.get('chat')['retired']

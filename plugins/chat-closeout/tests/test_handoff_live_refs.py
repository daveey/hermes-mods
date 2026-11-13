"""Legacy close-outs check "still open" against live state (self-heal agent-f82d813de9).

A done card's handoff is prose written at completion: "David still needs to approve the hosted-runner migration
(t_ask)". By the close-out turn that ask card may be done. Before this fix the prompt carried only the frozen
handoff, so the close-out relisted the finished ask as open ("I was wrong last turn ... the hosted-runner asks I
gave you were already done"). The prompt now re-reads each card a receipt-less handoff names and prints its live
status next to it.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_handoff_refs', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

ASK_ID = 't_01a1aaaabbbbcccc'
ASK = {"action": "Approve the hosted-runner migration in the GitHub org settings. Unblock = migrate; archive = keep.",
       "why_human": "org owner only", "interaction": "approval", "scope": "hosted runners",
       "evidence": "settings page"}
HANDOFF = (f"Migrated 3 of 4 repos. Still open: David must approve the hosted-runner migration ({ASK_ID}); "
           "checker shows 0 violations.")


def _batch():
    return {'t_main': {'status': 'done', 'title': 'Move CI to hosted runners', 'summary': HANDOFF}}


def _ask_card(status):
    card = {'id': ASK_ID, 'status': status, 'title': 'Approve hosted-runner migration',
            'summary': 'David approved; migration finished, 4/4 repos on hosted runners'}
    if status == 'blocked':
        card.update(block_kind='needs_input', human_request=json.dumps(ASK), summary=None)
    return card


def test_handoff_refs_names_cards_outside_the_batch_only():
    cards = {**_batch(), 't_01a1ddddeeeeffff': {'status': 'done', 'summary': 'see t_main'}}
    row = {'batch': ['t_main', 't_01a1ddddeeeeffff']}
    assert core.handoff_refs(row, cards) == [ASK_ID]


def test_done_ask_named_by_a_legacy_handoff_is_shown_done_not_open():
    named = {ASK_ID: _ask_card('done')}
    prompt = core.closeout_prompt({'batch': ['t_main']}, _batch(), named=named)
    line = next(ln for ln in prompt.splitlines() if ASK_ID in ln and 'live now' in ln)
    assert '[done]' in line and '4/4 repos' in line
    assert ASK['action'] not in prompt  # never re-relayed as an ask
    assert 'as of its completion' in prompt  # the handoff is labelled historical


def test_still_blocked_ask_named_by_a_handoff_stays_visible_as_open():
    named = {ASK_ID: _ask_card('blocked')}
    prompt = core.closeout_prompt({'batch': ['t_main']}, _batch(), named=named)
    line = next(ln for ln in prompt.splitlines() if ASK_ID in ln and 'live now' in ln)
    assert '[blocked' in line and 'needs the user' in line


def test_unreadable_named_card_is_said_unknown_not_open():
    prompt = core.closeout_prompt({'batch': ['t_main']}, _batch(), named={})
    line = next(ln for ln in prompt.splitlines() if ASK_ID in ln and 'live now' in ln)
    assert 'not found' in line


def test_wake_tick_rereads_named_cards_at_prompt_time(tmp_path, monkeypatch):
    """The waker path: the inventory comes from a read made for this prompt, not from the batch read alone."""
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    world = {**_batch(), ASK_ID: _ask_card('done')}
    reads = []

    def read(ids):
        reads.append(sorted(ids))
        return {i: world[i] for i in ids if i in world}

    calls = []
    led.write('chat', batch=['t_main'], open=['t_main'])
    plugin.wake_tick(now=10_000, wake=lambda sid, prompt, **kw: calls.append(prompt) or 'started', unseen={},
                     last_user=lambda _: 0, read=read)
    assert calls, reads
    assert [ASK_ID] in reads
    line = next(ln for ln in calls[0].splitlines() if ASK_ID in ln and 'live now' in ln)
    assert '[done]' in line

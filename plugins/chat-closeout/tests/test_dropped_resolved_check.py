"""Relay/close-out notices for an ask whose resolved_check was dropped by core (older cores never set the keys)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_dropped', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

ACTION = "Approve the rollout and I'll notice automatically when it is done. Unblock = go; archive = drop."
ASK = {"action": ACTION, "why_human": "needs a person", "interaction": "approval", "scope": "rollout only",
       "evidence": "dry run output"}
DROPPED = {**ASK, "resolved_check_dropped_at": 1700000000, "resolved_check_drop_reason": "resolved_check_lost"}


def _cards(hr):
    return {'t_ask': {'status': 'blocked', 'block_kind': 'needs_input', 'title': 'Rollout',
                      'human_request': json.dumps(hr)}}


def _warned(text):
    assert "Automatic detection stopped" in text and "probe went missing" in text
    assert "manual unblock" in text and "re-filed" in text
    assert "historical" in text and ACTION in text
    assert text.index("Automatic detection stopped") < text.index(ACTION)


def test_ask_notice_warns():
    _warned(core.ask_notice_text(_cards(DROPPED), ['t_ask']))


def test_closeout_notice_warns():
    _warned(core.notice_text({'batch': ['t_ask']}, _cards(DROPPED)))


def test_prompts_warn():
    row = {'batch': ['t_ask']}
    _warned(core.closeout_prompt(row, _cards(DROPPED)))
    _warned(core.ask_relay_prompt(row, _cards(DROPPED), ['t_ask']))


def test_remote_dict_projection_warns():
    cards = {'t_ask': {'status': 'blocked', 'title': 'Rollout', 'human_request': DROPPED}}
    _warned(core.ask_notice_text(cards, ['t_ask']))


def test_no_drop_keeps_old_output():
    text = core.ask_notice_text(_cards(ASK), ['t_ask'])
    assert text == f"Needs you: Rollout: {ACTION}"
    assert "Automatic detection" not in core.closeout_prompt({'batch': ['t_ask']}, _cards(ASK))


def test_drop_changes_premise_so_it_is_relayed_again():
    assert core.ask_premise('t_ask', _cards(ASK)['t_ask']) != core.ask_premise('t_ask', _cards(DROPPED)['t_ask'])

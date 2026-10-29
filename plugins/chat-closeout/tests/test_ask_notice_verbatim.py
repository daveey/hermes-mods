"""Close-out and ask-relay notices for user-blocked cards carry the card's ask line verbatim (coach pattern
blocked-card-missing-user-ask): the user saw "2 waiting on you" / "Needs you: <title>" and could not tell what to do."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_ask_notice', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

# Long on purpose: the old ask line truncated at 400 chars and the relay notice at 160.
ACTION = ("Say yes or no to switching the mini's unattended approvals to deny and blocking lock/cover/alarm in the "
          "Home Assistant plugin. Unblock = do both halves now (saying 'open the garage' to Baby Bear keeps working); "
          "archive = leave both as they are and record the accepted risk. " + "Details: " + "x" * 300)
ASK = {"action": ACTION, "why_human": "changes what unattended agents may do", "interaction": "approval",
       "scope": "mini configs only", "evidence": "config keys read on the mini"}


def _cards():
    return {'t_ask': {'status': 'blocked', 'block_kind': 'needs_input', 'title': 'Mini safety change',
                      'human_request': json.dumps(ASK)},
            't_done': {'status': 'done', 'title': 'Report', 'summary': 'delivered'},
            't_review': {'status': 'review', 'title': 'Under review'}}


def test_closeout_notice_carries_ask_verbatim():
    row = {'batch': ['t_ask', 't_done', 't_review']}
    text = core.notice_text(row, _cards())
    assert '1 waiting on you' in text  # review is not waiting on the user
    assert ACTION in text


def test_ask_relay_notice_carries_ask_verbatim():
    text = core.ask_notice_text(_cards(), ['t_ask'])
    assert 'Mini safety change' in text and ACTION in text


def test_closeout_and_relay_prompts_carry_full_ask():
    row = {'batch': ['t_ask', 't_done']}
    assert ACTION in core.closeout_prompt(row, _cards())
    assert ACTION in core.ask_relay_prompt(row, _cards(), ['t_ask'])
    assert 'archiving' in core.closeout_prompt(row, _cards())


def test_blocked_card_without_ask_still_says_so():
    cards = {'t_bare': {'status': 'blocked', 'block_kind': 'needs_input', 'title': 'Legacy block'}}
    text = core.notice_text({'batch': ['t_bare']}, cards)
    assert '1 waiting on you' in text and 'names no ask' in text


def test_no_user_block_keeps_short_notice():
    cards = {'t_done': {'status': 'done', 'title': 'Report'}}
    assert core.notice_text({'batch': ['t_done']}, cards) == 'Close-out: the 1 card this chat waited on settled'

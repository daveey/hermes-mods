"""publication_buffer on a turn whose tools the coach refused because the user said stop (self-heal agent-e9ebbbb4cd).

Hub live probe for t_01a1e9a61bf16b6f (session local-41b9e1e8…, 2026-11-17): the user wrote "stop. answer my
question: …". The agent's answer offered a follow-up ("want me to read it?"); this gate held it and nudged "publish
action_request", coach answer_first refused that tool (the user said stop), and at the budget the answer was
replaced by NOT_READY: the user got neither his answer nor a request. In the original incident the same nudge is the
likely driver (hypothesis: holds are not traced) of the tool_describe + action_request calls after "STOP DOING
THINGS< ANSWER MY FUCKING QUESTIOM". A turn whose tools are refused cannot publish, so the answer goes out with the
no-request note instead of a hold or a replacement.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = {'gatewayId': 'test', 'profile': 'default', 'storedSessionId': 'session'}
ANSWER = ("I don't know: I haven't read that file this session. Want me to read it and quote the entry?")
STOP_BLOCK = ('{"error": "[coach answer_first] The user told you to stop and answer. Do not run any tool this turn '
              '(this call was not run, and no other will be)."}')
STOP_TURN = [
    {'role': 'user', 'content': 'stop. answer my question: which file is the largest?'},
    {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1'}]},
    {'role': 'tool', 'content': STOP_BLOCK},
    {'role': 'assistant', 'content': ANSWER},
]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    mod = load('stop_turn_plugin', ROOT / '__init__.py')
    st = mod.core.ActionStore(tmp_path / 'actions.sqlite3')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    monkeypatch.setattr(mod.core, 'server_owner', lambda _: OWNER)
    monkeypatch.setattr(mod.core, 'ActionStore', lambda **kw: st)
    monkeypatch.setattr(mod.core, '_worth_asking', lambda _: True)
    monkeypatch.setattr(mod.core, 'extract', lambda *_, **__: {
        'steps': [{'kind': 'approve', 'text': 'Want me to read it and quote the entry?'}]})
    return mod


@pytest.mark.parametrize('attempt', [0, 1, 2])
def test_stop_turn_answer_is_shown_with_note_never_held_or_replaced(plugin, attempt):
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=ANSWER,
                                    turn_messages=STOP_TURN, attempt=attempt, hold_budget=2)
    assert out is not None and not out.get('hold') and 'replace' not in out, out
    assert 'no permission has been granted' in out['append']


def test_other_tool_errors_still_hold(plugin):
    ctx = STOP_TURN[:2] + [{'role': 'tool', 'content': '{"error": "[coach answer_first] The user asked a question '
                                                       'and has had no answer for 6 tool calls / 130 s."}'}] + \
        STOP_TURN[3:]
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=ANSWER,
                                    turn_messages=ctx, attempt=0, hold_budget=2)
    assert out['hold'] and out['nudge'] == plugin.PUBLISH_NUDGE

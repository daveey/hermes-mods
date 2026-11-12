"""Turn-end visibility: a card the chat created this turn must show in the chat's own task list before the turn
ends (defect agent-bb942f6be9). On 2026-11-10 a MacBook chat dispatched hub card t_01a1c7b952ec8cfa, ended with
"the chat-link acknowledgement is still pending", and the chat's task list was empty while the replica diagnostic
showed the link. The gate keys on this turn's kanban_create results and reads core's ``session_tasks`` (the same
projection behind /api/session-kanban-tasks), never the reply's wording."""
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_visibility', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

CHAT = 'local-d98f25deffa69af45340dda64c25a26ed6aabcb81264a445f78099c923071e15'
HUB_CARD = 't_01a1c7b952ec8cfa'


def _create_turn(result: dict, name: str = 'kanban_create', args: dict | None = None) -> list[dict]:
    args = args if args is not None else {'title': 'fix the thing'}
    return [
        {'role': 'user', 'content': 'dispatch a card for this'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]},
        {'role': 'tool', 'tool_call_id': 'call_1', 'content': json.dumps(result)},
    ]


HUB_RESULT = {'ok': True, 'task_id': HUB_CARD, 'status': 'created', 'runs_where': 'hermes-hub',
              'placed': {'gateway': 'hermes-hub', 'request_id': 'r1', 'task_id': HUB_CARD, 'origin_session': CHAT},
              'origin_binding': {'readback': 'pending', 'verify_on_origin': 'hermes-hub links x --expect y'}}


@pytest.fixture
def gate(monkeypatch):
    """pre_final_gate with a fake core ``session_tasks``; ``reads`` records every call."""
    for key in ('HERMES_KANBAN_TASK', 'CHAT_CLOSEOUT_OFF'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_backed', lambda _: True)
    monkeypatch.setattr(plugin, '_visibility_sleep', lambda s: None, raising=False)
    state = types.SimpleNamespace(answers=[], reads=[])

    def session_tasks(ids, statuses=(), **_):
        state.reads.append((list(ids), tuple(statuses)))
        answer = state.answers.pop(0) if len(state.answers) > 1 else state.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    ksa = types.SimpleNamespace(session_tasks=session_tasks, LISTED_STATUSES=('todo', 'ready', 'running'),
                                ACTIVE_STATUSES=('todo', 'ready', 'running'))
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', ksa)
    pkg = sys.modules.get('hermes_cli')
    if pkg is None:
        pkg = types.ModuleType('hermes_cli')
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, 'hermes_cli', pkg)
    monkeypatch.setattr(pkg, 'kanban_session_activity', ksa, raising=False)
    return state


def _run(turn, attempt=0, reply='Dispatched t_01a1c7b952ec8cfa on the hub.'):
    return plugin.pre_final_gate(session_id=CHAT, platform='desktop', final_response=reply, turn_messages=turn,
                                 attempt=attempt)


def test_created_hub_card_missing_from_task_list_is_held(gate, caplog):
    gate.answers = [[]]
    caplog.set_level(logging.INFO)
    out = _run(_create_turn(HUB_RESULT))
    assert out and out.get('hold') is True
    assert HUB_CARD in out['nudge'] and core.TAG in out['nudge']
    assert f'chat-closeout: turn-end visibility held card={HUB_CARD}' in caplog.text


def test_second_attempt_appends_visible_warning(gate):
    gate.answers = [[]]
    out = _run(_create_turn(HUB_RESULT), attempt=1)
    assert out == {'append': f"⚠ {HUB_CARD} isn't shown in this chat's task list yet."}


def test_listed_card_passes(gate, caplog):
    gate.answers = [[{'id': HUB_CARD, 'key': f'hermes-hub/default/{HUB_CARD}', 'status': 'ready'}]]
    caplog.set_level(logging.INFO)
    assert _run(_create_turn(HUB_RESULT)) is None
    assert f'chat-closeout: turn-end visibility ok card={HUB_CARD}' in caplog.text
    ids, statuses = gate.reads[0]
    assert ids == [CHAT] and 'done' in statuses  # a card finished within the turn is not "missing"


def test_replica_lag_rechecked_before_holding(gate):
    gate.answers = [[], [{'id': HUB_CARD, 'status': 'ready'}]]
    assert _run(_create_turn(HUB_RESULT)) is None
    assert len(gate.reads) == 2


def test_deferred_tool_call_create_is_checked(gate):
    gate.answers = [[]]
    wrapped = {'results': [{'name': 'kanban_create', 'result': json.dumps(HUB_RESULT)}]}
    turn = _create_turn(wrapped, name='tool_call',
                        args={'calls': [{'name': 'kanban_create', 'arguments': {'title': 'x'}}]})
    out = _run(turn)
    assert out and out.get('hold') is True and HUB_CARD in out['nudge']


def test_read_error_passes(gate):
    gate.answers = [OSError('board busy')]
    assert _run(_create_turn(HUB_RESULT)) is None


def test_no_create_no_read(gate):
    turn = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'hello'}]
    assert _run(turn, reply='hello') is None
    assert gate.reads == []


def test_failed_or_refused_create_is_ignored(gate):
    gate.answers = [[]]
    for result in ({'ok': False, 'error': 'create_overlap'}, {'ok': True, 'created': False, 'task_id': 't_x'},
                   {'ok': True, 'task_id': None, 'status': 'queued'}):
        assert _run(_create_turn(result)) is None
    assert gate.reads == []


def test_other_tools_returning_task_ids_are_ignored(gate):
    gate.answers = [[]]
    assert _run(_create_turn({'ok': True, 'task_id': 't_x'}, name='kanban_show')) is None
    assert gate.reads == []


def test_kill_switch_and_worker_skip(gate, monkeypatch):
    gate.answers = [[]]
    monkeypatch.setenv('CHAT_CLOSEOUT_OFF', '1')
    assert _run(_create_turn(HUB_RESULT)) is None
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF')
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_worker')
    assert _run(_create_turn(HUB_RESULT)) is None
    assert gate.reads == []


def test_promise_lint_and_visibility_hold_together(gate, monkeypatch):
    gate.answers = [[]]
    monkeypatch.setattr(plugin, '_backed', lambda _: False)
    out = _run(_create_turn(HUB_RESULT), reply="Dispatched it; I'll report back when it finishes.")
    assert out['hold'] is True and HUB_CARD in out['nudge'] and 'report' in out['nudge']


# ---- real core: the chat's task list is core's own session_tasks on a real board --------------------------------
@pytest.fixture
def real_board(tmp_path, monkeypatch):
    kb = pytest.importorskip('hermes_cli.kanban_db')
    from hermes_cli import kanban_session_activity as ksa
    for key in ('HERMES_KANBAN_TASK', 'HERMES_DELEGATED_CHILD_CONTEXT', 'HERMES_DELEGATED_CHILD_OWNER',
                'CHAT_CLOSEOUT_OFF', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'kanban.db'))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_backed', lambda _: True)
    monkeypatch.setattr(plugin, '_visibility_sleep', lambda s: None, raising=False)
    ksa._reset_cache_for_tests()
    yield kb
    ksa._reset_cache_for_tests()


def _real_create(kb, session_id):
    from hermes_cli.kanban_db_connect import connect_closing
    with connect_closing() as conn:
        tid = kb.create_task(conn, title='fix', session_id=session_id)
        conn.commit()
    return tid


def test_real_core_card_not_linked_to_desktop_chat_is_held(real_board):
    """The incident's shape on core's own schema: the card exists but carries another session (the Mac's
    pre-7026994c classifier dropped the local-<sha> chat), so the chat's task list is empty for it."""
    tid = _real_create(real_board, 'some-machine-session')
    out = _run(_create_turn({'ok': True, 'task_id': tid, 'status': 'ready'}))
    assert out and out.get('hold') is True and tid in out['nudge']


def test_real_core_card_linked_to_chat_passes(real_board):
    tid = _real_create(real_board, CHAT)
    assert _run(_create_turn({'ok': True, 'task_id': tid, 'status': 'ready'})) is None

"""Sanitized text-stop replay; no provider requests or live profile writes."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('goal_coach_test', ROOT / '__init__.py')
coach_plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coach_plugin)


def verdict():
    return dict(goal='Repair example app', actionable=True, unfinished=True,
                disposition='abandoned', evidence_refs=['user:0', 'candidate'],
                boundary_kind=None, continuation_ref=None, confidence=0.99)


@pytest.mark.parametrize('recheck_error', [False])
@pytest.mark.parametrize('discovered', [True])
@pytest.mark.parametrize('platform', ['cli', 'desktop'])
def test_diagnosis_only_text_stop_is_held_before_delivery(tmp_path, monkeypatch, platform, discovered, recheck_error):
    from run_agent import AIAgent
    def no_aux_network(*a, **kw):
        raise RuntimeError('isolated review blocks auxiliary network')
    monkeypatch.setattr('agent.auxiliary_client.call_llm', no_aux_network)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_VERIFY_ON_STOP', '0')
    monkeypatch.setenv('COACH_LESSONS', '0')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    (tmp_path / 'coach').mkdir()
    (tmp_path / 'coach' / 'goal-disposition.json').write_text('{"mode":"enforce"}')
    _, hooks = coach_plugin.build(store=coach_plugin._load('core').Store(tmp_path / 'coach'))
    # Missing hook is the original failure: no diagnosis-only gate exists.
    hook = hooks.get('pre_final_gate', lambda **kw: None)
    checks = []
    def judge(*_):
        checks.append(True)
        if recheck_error and len(checks) > 1:
            if recheck_error == 'unknown':
                return dict(verdict(), disposition='unknown')
            if recheck_error == 'low_confidence':
                return dict(verdict(), confidence=.1)
            raise TimeoutError('synthetic provider timeout')
        return verdict()
    if 'pre_final_gate' in hooks:
        monkeypatch.setattr(coach_plugin._load('goal_disposition'), 'aux_judge', judge)
    with patch('model_tools.get_tool_definitions', return_value=[]), \
         patch('model_tools.check_toolset_requirements', return_value={}), \
         patch('agent.process_bootstrap.OpenAI'):
        agent = AIAgent(session_id='example-session', api_key='test-key',
                        base_url='https://example.invalid/v1', provider='openai-compat',
                        model='test/model', max_iterations=1, quiet_mode=True,
                        skip_context_files=True, skip_memory=True, platform=platform)
    agent._cached_system_prompt = 'stable test prompt'
    agent._session_db = None
    agent.save_trajectories = False
    agent.compression_enabled = False
    agent._cleanup_task_resources = lambda *_a, **_kw: None
    agent._save_trajectory = lambda *_a, **_kw: None
    screen, calls = [], []
    diagnosis = 'The app remains HTTP 503. Run `sudo systemctl restart example` on the server.'
    def api(kw):
        calls.append(kw)
        text = diagnosis
        agent._fire_stream_delta(text)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=text, tool_calls=None), finish_reason='stop')], model='test/model', usage=None)
    agent.stream_delta_callback = lambda d: screen.append(d) if isinstance(d, str) else None
    agent._interruptible_api_call = api
    agent._interruptible_streaming_api_call = lambda kw, on_first_delta=None: api(kw)
    from contextlib import ExitStack
    with ExitStack() as stack:
        if discovered:
            from hermes_cli.plugins import get_plugin_manager
            monkeypatch.setenv('COACH_REPLY_RULES', 'none')
            (tmp_path / 'plugins').mkdir()
            (tmp_path / 'plugins' / 'coach').symlink_to(ROOT, target_is_directory=True)
            (tmp_path / 'plugins' / 'ask-gate').symlink_to(ROOT.parent / 'ask-gate', target_is_directory=True)
            (tmp_path / 'config.yaml').write_text('plugins:\n  enabled: [coach, ask-gate]\n')
            manager = get_plugin_manager()
            manager.discover_and_load(force=True)
            stack.callback(manager.unload)
            print('ORDER', [h.__module__ + '.' + h.__qualname__ for h in manager.iter_hook_callbacks('pre_final_gate')])
            ask = manager._plugins['ask-gate'].module
            monkeypatch.setattr(ask, 'LLM', lambda *_a, **_kw: '[]')
            assert Path(manager._plugins['coach'].module.__file__).resolve().parent == ROOT
        else:
            stack.enter_context(patch('hermes_cli.lifecycle.has_hook', side_effect=lambda n: n == 'pre_final_gate'))
            stack.enter_context(patch('hermes_cli.plugins.iter_hook_callbacks', return_value=(hook,)))
            stack.enter_context(patch('hermes_cli.plugins.invoke_hook', return_value=[]))
        result = agent.run_conversation('Repair example app. Why have we not fixed it yet?')
    print('RESULT', result['final_response'], 'JUDGE_CALLS', len(checks), 'SCREEN', screen)
    assert checks, 'Coach was never consulted before finalization'
    assert 'Unresolved goal' in result['final_response']
    assert diagnosis not in result['final_response']
    assert diagnosis not in ''.join(screen)
    if recheck_error:
        assert 'continuation unavailable' in result['final_response']
    assert not any(m.get('_pre_final_gate_synthetic') for m in result['messages'])



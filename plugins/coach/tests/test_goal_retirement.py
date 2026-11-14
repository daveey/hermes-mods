"""Retirement removes only the disposition gate, with no judge or buffer."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

pytest.importorskip('hermes_constants')

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retirement_coach', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


def configure(tmp_path, monkeypatch, mode='retired'):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'coach').mkdir(exist_ok=True)
    (tmp_path / 'coach/goal-disposition.json').write_text('{"mode":"' + mode + '"}')
    return plugin._load('goal_disposition')


def test_retired_callbacks_never_judge_or_capture(tmp_path, monkeypatch):
    gd = configure(tmp_path, monkeypatch)
    assert gd.settings()['mode'] == 'retired'
    def forbidden(*a, **kw):
        raise AssertionError('retired guard did work')
    monkeypatch.setattr(gd, 'aux_judge', forbidden)
    monkeypatch.setattr(gd, 'recover_scope', forbidden)
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    gate.capture(session_id='retirement', user_message='Fix app')
    gate.observe(session_id='retirement', tool_call_id='c', tool_name='terminal')
    assert gate(session_id='retirement', final_response='Still broken') is None
    assert not gate.needs_buffer
    assert events == []


def test_retirement_removes_registered_hook_but_preserves_other_guards(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    callbacks = []
    plugin.register(SimpleNamespace(register_hook=lambda n, f: callbacks.append((n, f))))
    final = [f for n, f in callbacks if n == 'pre_final_gate']
    assert [f.__name__ for f in final] == ['envelope_gate', 'status_fresh_gate']
    assert {'pre_llm_call', 'post_tool_call', 'pre_tool_call'} <= {n for n, f in callbacks}


def test_retired_gate_absent_after_normal_discovery(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    (tmp_path / 'plugins').mkdir()
    (tmp_path / 'plugins/coach').symlink_to(ROOT, target_is_directory=True)
    (tmp_path / 'config.yaml').write_text('plugins:\n  enabled: [coach]\n')
    from hermes_cli.plugins import get_plugin_manager
    manager = get_plugin_manager()
    try:
        manager.discover_and_load(force=True)
        assert Path(manager._plugins['coach'].module.__file__).resolve().parent == ROOT
        callbacks = list(manager.iter_hook_callbacks('pre_final_gate'))
        assert [f.__name__ for f in callbacks] == ['envelope_gate', 'status_fresh_gate']
    finally:
        manager.unload()


def test_retirement_does_not_accept_previously_held_reply(tmp_path, monkeypatch):
    gd = configure(tmp_path, monkeypatch, 'enforce')
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    (tmp_path / 'coach/goal-disposition.json').write_text('{"mode":"retired"}')
    import pytest
    with pytest.raises(ValueError, match='enforcing_recheck_unavailable'):
        gate(attempt=1)
    assert events[-1]['reason'] == 'enforcing_recheck_unavailable'

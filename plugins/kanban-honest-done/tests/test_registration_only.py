"""The producer-safe registrar declares companions without executing them."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _core():
    spec = importlib.util.spec_from_file_location('honest_registration_only', Path(__file__).parents[1] / 'core.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registration_only_handles_old_and_new_core_without_callback_execution():
    module = _core()
    assert module.register_with(SimpleNamespace()) is False
    seen = []

    def modern(name, fn, *, watch_paths, next_deadline):
        seen.append((name, fn, watch_paths, next_deadline))

    assert module.register_with(SimpleNamespace(register_done_check=modern)) is True
    assert seen == [('kanban-honest-done', module.needs_attention,
                     module.attention_watch_paths, module.attention_next_deadline)]
    seen.clear()

    def legacy(name, fn):
        seen.append((name, fn))

    assert module.register_with(SimpleNamespace(register_done_check=legacy)) is True
    assert seen == [('kanban-honest-done', module.needs_attention)]

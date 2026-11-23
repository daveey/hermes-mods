"""Event admission must not turn timer wakes into SQL or swallow trailing writes."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys


def load():
    path = Path(__file__).parents[1] / 'dashboard/change_source.py'
    spec = importlib.util.spec_from_file_location('change_source_test', path)
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_idle_never_captures_and_event_during_capture_is_trailing(monkeypatch):
    class Events:
        dirty = False
        def replace(self, paths):
            self.dirty = False
        def wait(self, timeout):
            assert timeout == 0
            dirty, self.dirty = self.dirty, False
            return dirty
    monkeypatch.setitem(sys.modules, 'hermes_cli.annotation_file_events', SimpleNamespace(FileEvents=Events))
    source = load().Source()
    calls = []
    def capture():
        calls.append(1)
        return len(calls), ('board.db',)
    assert source.read(capture) == 2  # discovery then covered capture
    for _ in range(10):
        assert source.read(capture) == 2
    assert len(calls) == 2
    source.events.dirty = True
    def racing():
        result = capture()
        source.events.dirty = True  # commit after covered snapshot
        return result
    assert source.read(racing) == 3
    assert source.read(capture) == 4


def test_metadata_fallback_captures_only_after_actual_file_change(tmp_path, monkeypatch):
    class Unsupported:
        def __init__(self):
            raise NotImplementedError
    monkeypatch.setitem(sys.modules, 'hermes_cli.annotation_file_events', SimpleNamespace(FileEvents=Unsupported))
    source = load().Source()
    p = tmp_path / 'board'
    p.write_text('old')
    calls = []
    def capture():
        calls.append(1)
        return p.read_text(), (str(p),)
    assert source.read(capture) == 'old'
    source.read(capture)  # initial trailing verification
    for _ in range(5):
        assert source.read(capture) == 'old'
    assert len(calls) == 2
    p.write_text('new')
    assert source.read(capture) == 'new'
    assert len(calls) == 3

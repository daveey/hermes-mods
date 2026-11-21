"""Shared test setup: a temp HERMES_HOME with one board built by core's kanban_db, and a scanner over it."""
import importlib.util
import sys
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fixtures():
    return load('_sr_fixtures', PLUGIN / 'tests' / 'fixtures' / 'patterns.py')


class Env:
    """One test's world: board (writable, the test's own), state.db, and `scan()` with the real readers."""

    def __init__(self, tmp_path, monkeypatch, *, max_in_progress=4):
        monkeypatch.setenv('HERMES_HOME', str(tmp_path))
        (tmp_path / 'config.yaml').write_text(
            f'kanban:\n  max_in_progress: {max_in_progress}\n'
            f'  sync:\n    key_file: {tmp_path / "missing-gateway.key"}\n')
        from hermes_cli.kanban_db_connect import connect
        self.home = tmp_path
        self.path = tmp_path / 'kanban.db'
        self.conn = connect(self.path)
        self.state = fixtures().state_db(tmp_path / 'state.db')
        self.now = time.time()
        self.scan_mod = load('_sr_scan', PLUGIN / 'scan.py')
        self.monkeypatch = monkeypatch

    def sources(self, now=None):
        api, _ = self.scan_mod.load_panel()
        readers = api.CoreReaders(state_db=self.home / 'state.db')
        self.monkeypatch.setattr(readers, 'boards', lambda: [('default', self.path)])
        return self.scan_mod.Sources(readers, now=self.now if now is None else now)

    def scan(self, now=None, rules=None):
        self.last_sources = self.sources(now)
        return self.scan_mod.scan(self.last_sources, rules=rules)

    def findings(self, rule=None, card=None, **kw):
        out = self.scan(**kw)['findings']
        return [f for f in out if (rule is None or f['id'] == rule) and (card is None or f['card'] == card)]

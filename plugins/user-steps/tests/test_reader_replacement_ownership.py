"""A late unload owns its generation, not the reusable import name."""
import importlib
import importlib.util
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_same_name_replacement_before_old_manager_unload(tmp_path, monkeypatch):
    plugins = pytest.importorskip('hermes_cli.plugins')
    name = 'user_steps_replacement_fixture'
    saved_modules = {key: value for key, value in sys.modules.items()
                     if key == name or key.startswith(name + '.')}
    managers = []
    mirrors = []
    path = tmp_path / 'reader.db'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE sample(value)')
        conn.execute('INSERT INTO sample VALUES (7)')

    def generation(index):
        # Evict the entire package, as a reload does, but retain its manager's
        # cleanup ledger until after the replacement has acquired its reader.
        for key in list(sys.modules):
            if key == name or key.startswith(name + '.'):
                sys.modules.pop(key)
        spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, '_require_publication_contract', lambda: None)
        monkeypatch.setattr(module, '_register_clarify_early', lambda: None)
        manager = plugins.PluginManager(scope_key=str(tmp_path / f'profile-{index}'))
        managers.append(manager)
        ctx = plugins.PluginContext(plugins.PluginManifest(name='user-steps'), manager)
        # Tool publication is unrelated to reader lifetime; keep the real
        # on_unload registration, ownership ledger and manager teardown.
        monkeypatch.setattr(ctx, 'register_tool', lambda **kwargs: None)
        module.register(ctx)
        mirror = importlib.import_module(name + '.kanban_mirror')
        mirrors.append(mirror)
        with mirror._kept(path) as reader:
            assert reader.execute('SELECT value FROM sample').fetchone()[0] == 7
        return module, manager, mirror, reader

    try:
        old, old_manager, old_mirror, old_reader = generation(0)
        new, new_manager, new_mirror, new_reader = generation(1)
        assert sys.modules[name] is new and new is not old
        assert old_mirror is not new_mirror
        assert old_reader is not new_reader
        assert old_reader.execute('SELECT 1').fetchone()[0] == 1
        assert new_reader.execute('SELECT 1').fetchone()[0] == 1

        assert old_manager.unload('user-steps')
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            old_reader.execute('SELECT 1')
        assert not old_mirror._POOL
        # Repeated old teardown must not steal the new generation's reader.
        old_manager.unload('user-steps')
        with new_mirror._kept(path) as retained:
            assert retained is new_reader
            assert retained.execute('SELECT value FROM sample').fetchone()[0] == 7
        assert new_manager.unload('user-steps')
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            new_reader.execute('SELECT 1')
        assert not new_mirror._POOL
    finally:
        for manager in managers:
            manager.unload('user-steps')
        for mirror in mirrors:
            mirror.close_pool()
        for key in list(sys.modules):
            if key == name or key.startswith(name + '.'):
                sys.modules.pop(key, None)
        sys.modules.update(saved_modules)

"""Concurrent dashboard loads must publish one reader-owning generation."""
import importlib.util
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from test_reader_lifecycle import ROOT, closed, db, load  # noqa: F401


@pytest.mark.parametrize('replace_existing', [False, True], ids=['cold', 'replacement'])
def test_concurrent_dashboard_load_serializes_reader_ownership(db, tmp_path, monkeypatch,
                                                             replace_existing):
    root = tmp_path / 'plugin'
    shutil.copytree(ROOT, root)
    api = load('concurrent_dashboard_api', root / 'dashboard/plugin_api.py')
    key = '_user_steps_kanban_mirror'
    monkeypatch.delitem(sys.modules, key, raising=False)
    old = None
    candidates, readers, disposals = [], [], []
    first_entered = threading.Event()
    contender_reached = threading.Event()
    resume = threading.Event()
    recording = threading.Lock()
    real_lock = api._LOAD_LOCK

    class ObservedLock:
        """Observe contention without replacing the production mutex."""
        def __enter__(self):
            if first_entered.is_set():
                contender_reached.set()
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    try:
        if replace_existing:
            old = api._load('kanban_mirror')
            with old._kept(db) as old_reader:
                assert old_reader.execute('SELECT value FROM sample').fetchone()[0] == 1
            real_close = old.close_pool

            def close_old():
                disposals.append(old)
                real_close()

            monkeypatch.setattr(old, 'close_pool', close_old)
            path = root / 'kanban_mirror.py'
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        def spec_for_candidate(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            real_loader = spec.loader

            class Loader:
                def create_module(self, spec):
                    return real_loader.create_module(spec)

                def exec_module(self, candidate):
                    real_loader.exec_module(candidate)
                    with candidate._kept(db) as reader:
                        assert reader.execute('SELECT value FROM sample').fetchone()[0] == 1
                    with recording:
                        candidates.append(candidate)
                        readers.append(reader)
                        first = len(candidates) == 1
                    if first:
                        first_entered.set()
                    else:
                        # Lock-deletion control reaches a second real candidate.
                        contender_reached.set()
                    assert resume.wait(5), 'candidate was not released'

            spec.loader = Loader()
            return spec

        # Scope instrumentation to this API, not the process-wide import machinery.
        monkeypatch.setattr(api, 'importlib', SimpleNamespace(util=SimpleNamespace(
            spec_from_file_location=spec_for_candidate,
            module_from_spec=importlib.util.module_from_spec)))
        monkeypatch.setattr(api, '_LOAD_LOCK', ObservedLock())
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(api._load, 'kanban_mirror')
            try:
                assert first_entered.wait(5), 'first candidate did not enter'
                second = pool.submit(api._load, 'kanban_mirror')
                assert contender_reached.wait(5), 'second loader did not contend'
                assert sys.modules.get(key) is old  # no partially loaded publication
            finally:
                resume.set()
            results = first.result(timeout=5), second.result(timeout=5)

        assert len(candidates) == 1, 'concurrent loaders created competing reader owners'
        assert results[0] is results[1] is candidates[0] is sys.modules[key]
        assert disposals == ([old] if replace_existing else [])
        if old is not None:
            closed(old_reader)
            assert not old._POOL
        assert len(readers) == 1
        assert readers[0].execute('SELECT value FROM sample').fetchone()[0] == 1
        candidates[0].close_pool()
        closed(readers[0])
        assert not candidates[0]._POOL
    finally:
        resume.set()
        for candidate in candidates:
            candidate.close_pool()
        if old is not None:
            old.close_pool()
        sys.modules.pop(key, None)

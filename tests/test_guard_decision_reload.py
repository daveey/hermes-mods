"""Exercise the real hook after editing only a temp-copy decision module."""
import importlib.util
import logging
import os
from pathlib import Path
import shutil
import sys
import uuid

import pytest

PLUGINS = "stash-worktree-guard push-guard mac-heavy-guard branch-pin-guard cpu-load-guard crontab-guard sqlite-live-guard live-checkout-guard".split()
ROOT = Path(__file__).resolve().parents[1]


def load_copy(tmp_path, name, monkeypatch, before_load=None):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    dest = tmp_path / name
    shutil.copytree(ROOT / "plugins" / name, dest, ignore=shutil.ignore_patterns("__pycache__"))
    if before_load:
        before_load(dest / "guard.py")
    key = "reload_probe_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(key, dest / "__init__.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, module)
    spec.loader.exec_module(module)
    hooks = {}
    class Context:
        def register_hook(self, name, fn):
            hooks[name] = fn
    module.register(Context())
    return module, hooks["pre_tool_call"], dest / "guard.py"


def mutation(marker):
    # Preserve the real module and change its decision entrypoints only.
    return f'''\ndef enabled(): return True
def check_shell(*args, **kwargs): return {marker!r}
def refuse_reason(*args, **kwargs): return {marker!r}
def evaluate(*args, **kwargs): return ({marker!r}, None)
def message(*args, **kwargs): return {marker!r}
'''


@pytest.mark.parametrize("name", PLUGINS)
def test_running_hook_refreshes_and_retains_last_good(tmp_path, monkeypatch, caplog, name):
    module, hook, path = load_copy(tmp_path, name, monkeypatch)
    args = {"command": "printf guard-reload-probe", "workdir": str(tmp_path)}
    assert hook(tool_name="terminal", args=args) is None
    source = path.read_text()
    caplog.set_level(logging.INFO)
    path.write_text(source + mutation("reload-refused"))
    result = hook(tool_name="terminal", args=args)
    assert result and result["action"] == "block", "loaded worker retained old allowed decision"
    assert result["message"] == "reload-refused"
    assert [r.message for r in caplog.records if ": reloaded " in r.message] == [f"{name}: reloaded guard.py"]
    last_good = module.guard
    path.write_text("def broken(:\n")
    for _ in range(2):
        assert hook(tool_name="terminal", args=args)["message"] == "reload-refused"
        assert module.guard is last_good
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    path.write_text("def another_broken(:\n")
    assert hook(tool_name="terminal", args=args)["message"] == "reload-refused"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
    path.unlink()
    for _ in range(2):
        assert hook(tool_name="terminal", args=args)["message"] == "reload-refused"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3
    path.write_text(source)
    assert hook(tool_name="terminal", args=args) is None
    assert len([r for r in caplog.records if ": reloaded " in r.message]) == 2


@pytest.mark.parametrize("name", PLUGINS)
def test_same_second_same_size_does_not_use_stale_bytecode(tmp_path, monkeypatch, name):
    module, hook, path = load_copy(tmp_path, name, monkeypatch)
    source = path.read_text()
    args = {"command": "printf guard-reload-probe", "workdir": str(tmp_path)}
    path.write_text(source + mutation("version-A"))
    import py_compile
    py_compile.compile(str(path), doraise=True)
    stamp = path.stat()
    assert hook(tool_name="terminal", args=args)["message"] == "version-A"
    path.write_text(source + mutation("version-B"))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + (1 if stamp.st_mtime_ns % 1_000_000_000 < 999_999_999 else -1)))
    assert path.stat().st_size == stamp.st_size
    assert hook(tool_name="terminal", args=args)["message"] == "version-B"


@pytest.mark.parametrize("name", PLUGINS)
def test_initial_activation_ignores_stale_pyc(tmp_path, monkeypatch, name):
    def prepare(path):
        import py_compile
        source = path.read_text()
        path.write_text(source + mutation("version-A"))
        py_compile.compile(str(path), doraise=True)
        stamp = path.stat()
        path.write_text(source + mutation("version-B"))
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    _, hook, _ = load_copy(tmp_path, name, monkeypatch, prepare)
    assert hook(tool_name="terminal", args={"command": "printf probe", "workdir": str(tmp_path)})["message"] == "version-B"


@pytest.mark.parametrize("name", PLUGINS)
def test_edit_during_reload_retains_previous_then_retries(tmp_path, monkeypatch, name):
    module, hook, path = load_copy(tmp_path, name, monkeypatch)
    source = path.read_text()
    previous = module.guard
    args = {"command": "printf probe", "workdir": str(tmp_path)}
    path.write_text(source + mutation("version-A"))
    original = module._load_decision
    def racing_load():
        fresh = original()
        path.write_text(source + mutation("version-B"))
        return fresh
    monkeypatch.setattr(module, "_load_decision", racing_load)
    assert hook(tool_name="terminal", args=args) is None
    assert module.guard is previous
    monkeypatch.setattr(module, "_load_decision", original)
    assert hook(tool_name="terminal", args=args)["message"] == "version-B"


@pytest.mark.parametrize("name", PLUGINS)
def test_verdict_keeps_one_generation_during_concurrent_reload(tmp_path, monkeypatch, name):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    module, hook, path = load_copy(tmp_path, name, monkeypatch)
    source = path.read_text()
    args = {"command": "printf probe", "workdir": str(tmp_path)}
    path.write_text(source + mutation("version-A"))
    assert hook(tool_name="terminal", args=args)["message"] == "version-A"
    entered, release, contender = Event(), Event(), Event()
    decision = "evaluate" if name == "mac-heavy-guard" else "refuse_reason" if name == "stash-worktree-guard" else "check_shell"
    original = getattr(module.guard, decision)
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(module.guard, decision, paused)
    def second_call():
        contender.set()
        return hook(tool_name="terminal", args=args)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hook, tool_name="terminal", args=args)
        try:
            assert entered.wait(10)
            path.write_text(source + mutation("version-B"))
            second = pool.submit(second_call)
            assert contender.wait(10)
            # The verdict owns the same lock that guards publication.
            acquired = module._decision_lock.acquire(blocking=False)
            if acquired:
                module._decision_lock.release()
            assert not acquired
        finally:
            release.set()
        assert first.result(timeout=10)["message"] == "version-A"
        assert second.result(timeout=10)["message"] == "version-B"

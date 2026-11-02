"""Exercise real hooks with stdlib unittest, the repo-guards CI runner."""
import importlib.util
import logging
import os
from pathlib import Path
import py_compile
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

PLUGINS = "stash-worktree-guard push-guard mac-heavy-guard branch-pin-guard cpu-load-guard crontab-guard sqlite-live-guard live-checkout-guard".split()
ROOT = Path(__file__).resolve().parents[1]


def mutation(marker):
    # Preserve the real module and change its decision entrypoints only.
    return f'''\ndef enabled(): return True
def check_shell(*args, **kwargs): return {marker!r}
def refuse_reason(*args, **kwargs): return {marker!r}
def evaluate(*args, **kwargs): return ({marker!r}, None)
def message(*args, **kwargs): return {marker!r}
'''


class ReloadCases(unittest.TestCase):
    name: str

    def setUp(self):
        self.tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.args = {"command": "printf guard-reload-probe", "workdir": str(self.tmp_path)}

    def load_copy(self, before_load=None):
        home = self.tmp_path / "home"
        home.mkdir()
        self.enterContext(patch.dict(os.environ, HERMES_HOME=str(home), TERMINAL_CWD=str(self.tmp_path)))
        dest = self.tmp_path / self.name
        shutil.copytree(ROOT / "plugins" / self.name, dest, ignore=shutil.ignore_patterns("__pycache__"))
        if before_load:
            before_load(dest / "guard.py")
        key = "reload_probe_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(key, dest / "__init__.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Restore only our key, not unrelated imports made by native dependencies.
        sys.modules[key] = module
        self.addCleanup(sys.modules.pop, key, None)
        spec.loader.exec_module(module)
        hooks = {}
        class Context:
            def register_hook(self, name, fn):
                hooks[name] = fn
        module.register(Context())
        return module, hooks["pre_tool_call"], dest / "guard.py"

    def test_running_hook_refreshes_and_retains_last_good(self):
        module, hook, path = self.load_copy()
        assert hook(tool_name="terminal", args=self.args) is None
        source = path.read_text()
        with self.assertLogs(level=logging.INFO) as captured:
            path.write_text(source + mutation("reload-refused"))
            result = hook(tool_name="terminal", args=self.args)
            assert result and result["action"] == "block", "loaded worker retained old allowed decision"
            assert result["message"] == "reload-refused"
            assert [r.getMessage() for r in captured.records if ": reloaded " in r.getMessage()] == [f"{self.name}: reloaded guard.py"]
            last_good = module.guard
            path.write_text("def broken(:\n")
            for _ in range(2):
                assert hook(tool_name="terminal", args=self.args)["message"] == "reload-refused"
                assert module.guard is last_good
            assert len([r for r in captured.records if r.levelno == logging.WARNING]) == 1
            path.write_text("def another_broken(:\n")
            assert hook(tool_name="terminal", args=self.args)["message"] == "reload-refused"
            assert len([r for r in captured.records if r.levelno == logging.WARNING]) == 2
            path.unlink()
            for _ in range(2):
                assert hook(tool_name="terminal", args=self.args)["message"] == "reload-refused"
            assert len([r for r in captured.records if r.levelno == logging.WARNING]) == 3
            path.write_text(source)
            assert hook(tool_name="terminal", args=self.args) is None
            assert len([r for r in captured.records if ": reloaded " in r.getMessage()]) == 2

    def test_same_second_same_size_does_not_use_stale_bytecode(self):
        _, hook, path = self.load_copy()
        source = path.read_text()
        path.write_text(source + mutation("version-A"))
        py_compile.compile(str(path), doraise=True)
        stamp = path.stat()
        assert hook(tool_name="terminal", args=self.args)["message"] == "version-A"
        path.write_text(source + mutation("version-B"))
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + (1 if stamp.st_mtime_ns % 1_000_000_000 < 999_999_999 else -1)))
        assert path.stat().st_size == stamp.st_size
        assert hook(tool_name="terminal", args=self.args)["message"] == "version-B"

    def test_initial_activation_ignores_stale_pyc(self):
        def prepare(path):
            source = path.read_text()
            path.write_text(source + mutation("version-A"))
            py_compile.compile(str(path), doraise=True)
            stamp = path.stat()
            path.write_text(source + mutation("version-B"))
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        _, hook, _ = self.load_copy(prepare)
        assert hook(tool_name="terminal", args=self.args)["message"] == "version-B"

    def test_edit_during_reload_retains_previous_then_retries(self):
        module, hook, path = self.load_copy()
        source = path.read_text()
        previous = module.guard
        path.write_text(source + mutation("version-A"))
        original = module._load_decision
        def racing_load():
            fresh = original()
            path.write_text(source + mutation("version-B"))
            return fresh
        with patch.object(module, "_load_decision", racing_load):
            assert hook(tool_name="terminal", args=self.args) is None
            assert module.guard is previous
        assert hook(tool_name="terminal", args=self.args)["message"] == "version-B"

    def test_verdict_keeps_one_generation_during_concurrent_reload(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        module, hook, path = self.load_copy()
        source = path.read_text()
        path.write_text(source + mutation("version-A"))
        assert hook(tool_name="terminal", args=self.args)["message"] == "version-A"
        entered, release, contender = Event(), Event(), Event()
        decision = "evaluate" if self.name == "mac-heavy-guard" else "refuse_reason" if self.name == "stash-worktree-guard" else "check_shell"
        original = getattr(module.guard, decision)
        def paused(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)
        self.enterContext(patch.object(module.guard, decision, paused))
        def second_call():
            contender.set()
            return hook(tool_name="terminal", args=self.args)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(hook, tool_name="terminal", args=self.args)
            try:
                assert entered.wait(10)
                path.write_text(source + mutation("version-B"))
                second = pool.submit(second_call)
                assert contender.wait(10)
                acquired = module._decision_lock.acquire(blocking=False)
                if acquired:
                    module._decision_lock.release()
                assert not acquired
            finally:
                release.set()
            assert first.result(timeout=10)["message"] == "version-A"
            assert second.result(timeout=10)["message"] == "version-B"


# Each guard/scenario is a separately reported unittest case, not a hidden loop.
for _name in PLUGINS:
    _class_name = "TestReload_" + _name.replace("-", "_")
    globals()[_class_name] = type(
        _class_name, (ReloadCases,), {"name": _name, "__module__": __name__}
    )
# The template has no guard name; only its concrete subclasses are collected.
del ReloadCases

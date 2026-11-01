"""Sibling caches belong to a source checkout, not a process-wide bare name."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest


def file_module(path):
    spec = importlib.util.spec_from_file_location("sibling_probe", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("entry,target,method", [
    ("core.py", "curriculum.py", "curriculum_mod"),
    ("dashboard/plugin_api.py", "core.py", "_core"),
])
def test_sibling_entrypoints_isolate_checkouts(tmp_path, entry, target, method):
    roots = [tmp_path / name for name in ("first", "second")]
    for root, value in zip(roots, (1, 2)):
        (root / entry).parent.mkdir(parents=True, exist_ok=True)
        (root / entry).write_bytes((HERE / entry).read_bytes())
        # A helper, when present, must come from the same checkout as the caller.
        for helper in ("module_cache.py", "__init__.py"):
            if (HERE / helper).exists():
                (root / helper).write_bytes((HERE / helper).read_bytes())
        (root / target).write_text(f"VALUE = {value}\n")
        os.utime(root / target, ns=(1_000_000_000, 1_000_000_000))
    first, second = [getattr(file_module(root / entry), method) for root in roots]
    a, b = first(), second()
    assert (a.VALUE, b.VALUE) == (1, 2)
    a.patched = object()
    assert first() is a and second() is b and a is not b
    alias = tmp_path / "alias"
    alias.symlink_to(roots[0], target_is_directory=True)
    assert getattr(file_module(alias / entry), method)() is a
    assert loader(roots[0])._load(Path(target).stem) is a
    (roots[0] / target).write_text("VALUE = 3\n")
    os.utime(roots[0] / target, ns=(3_000_000_000, 3_000_000_000))
    updated = first()
    assert updated is not a and updated.VALUE == 3
    assert second() is b and b.VALUE == 2

HERE = Path(__file__).resolve().parents[1]


def loader(root):
    spec = importlib.util.spec_from_file_location("cache_probe", root / "__init__.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_failed_load_does_not_cache_partial_module(tmp_path):
    plugin = loader(HERE)
    for _ in range(2):
        with pytest.raises(FileNotFoundError):
            plugin._cache.load_sibling(tmp_path, "missing")
    assert plugin._cache.module_key(tmp_path, "missing") not in sys.modules


def test_block_time_uses_loaded_second_opinion(monkeypatch):
    plugin = loader(HERE)
    so = plugin._load("second_opinion")
    reply = plugin._load("reply_check")
    monkeypatch.delenv("COACH_BLOCK_TIME", raising=False)
    monkeypatch.setattr(so, "_config_settings", lambda: {"block_time": False})
    assert reply.block_time_enabled() is False
    assert reply.block_time_enabled() is False


def test_equal_mtimes_do_not_share_modules_and_same_root_reloads(tmp_path):
    roots = [tmp_path / name for name in ("first", "second")]
    for root, value in zip(roots, (1, 2)):
        root.mkdir()
        (root / "__init__.py").write_bytes((HERE / "__init__.py").read_bytes())
        (root / "module_cache.py").write_bytes((HERE / "module_cache.py").read_bytes())
        (root / "probe.py").write_text(f"VALUE = {value}\n")
        os.utime(root / "probe.py", ns=(1_000_000_000, 1_000_000_000))
    first, second = map(loader, roots)
    a, b = first._load("probe"), second._load("probe")
    assert a is not b
    assert (a.VALUE, b.VALUE) == (1, 2)
    assert loader(roots[0])._load("probe") is a  # fresh plugin load, same canonical source
    alias = tmp_path / "alias"
    alias.symlink_to(roots[0], target_is_directory=True)
    assert loader(alias)._load("probe") is a
    (roots[0] / "probe.py").write_text("VALUE = 3\n")
    os.utime(roots[0] / "probe.py", ns=(3_000_000_000, 3_000_000_000))
    updated = first._load("probe")
    assert updated is not a and updated.VALUE == 3
    assert second._load("probe") is b and b.VALUE == 2

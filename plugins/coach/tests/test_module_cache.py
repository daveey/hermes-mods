"""Sibling caches belong to a source checkout, not a process-wide bare name."""
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def loader(root):
    spec = importlib.util.spec_from_file_location("cache_probe", root / "__init__.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_equal_mtimes_do_not_share_modules_and_same_root_reloads(tmp_path):
    roots = [tmp_path / name for name in ("first", "second")]
    for root, value in zip(roots, (1, 2)):
        root.mkdir()
        (root / "__init__.py").write_bytes((HERE / "__init__.py").read_bytes())
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

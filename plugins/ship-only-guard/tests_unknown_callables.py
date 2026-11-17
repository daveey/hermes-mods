"""Unknown target-expression semantics must preserve broad support overlap."""
import subprocess
import sys

import pytest
from tests_support_imports import SUPPORT, changed


@pytest.mark.parametrize('expression', [
    "''.join(['placement_', 'support'])", "'_'.join(['placement', 'support'])",
])
def test_review_join_program_breaks_but_preflight_refuses(monkeypatch, tmp_path, expression):
    text = f'from importlib import import_module\nassert import_module({expression}).VALUE == 1\n'
    script = tmp_path / 'test_x.py'
    script.write_text(text)
    support = tmp_path / 'placement_support.py'
    support.write_text('VALUE = 1\n')
    before = subprocess.run([sys.executable, '-B', str(script)], capture_output=True, text=True)
    support.write_text('VALUE = 2\n')
    after = subprocess.run([sys.executable, '-B', str(script)], capture_output=True, text=True)
    assert before.returncode == 0, before.stderr
    assert after.returncode == 1 and 'AssertionError' in after.stderr
    why = changed({'tests/__init__.py': '', SUPPORT: 'VALUE = 1\n', 'tests/test_x.py': text}, monkeypatch)
    assert why and 'shared ~ tests/placement_support.py' in why


# A corpus, not a list of forbidden names: no member has a certified receiver.
# Include the review's two actual str.join exploits and every old path exemption.
UNKNOWN = [
    "''.join(['placement_', 'support'])",
    "'_'.join(['placement', 'support'])",
    "getattr('x', 'join')(['placement', 'support'])",
    "(lambda x: x)('json')",
    *[f"unknown.{method}('json')" for method in (
        'str', 'Path', 'PurePath', 'PosixPath', 'PurePosixPath', 'fspath',
        'abspath', 'realpath', 'normpath', 'expanduser', 'dirname', 'basename',
        'join', 'resolve', 'absolute', 'with_name', 'with_suffix', 'joinpath')],
    "unknown('json').parent",
    "unknown('json').parents[0]",
    "NAME",
    "'j' + 'son'",
    "f'placement_{1+2}_support'",
    "str('json')",
    "Path('json')",
    "os.path.join('scripts', 'other.py')",
    "'json' if flag else 'time'",
]


@pytest.mark.parametrize('expression', UNKNOWN)
def test_unknown_target_expression_always_keeps_broad_overlap(monkeypatch, expression):
    tip = {'tests/__init__.py': '', SUPPORT: 'VALUE = 1\n',
           'tests/test_x.py': 'import bridge\n',
           'tests/bridge.py': f'import importlib\nimportlib.import_module({expression})\n'}
    why = changed(tip, monkeypatch)
    assert why and 'shared ~ tests/placement_support.py' in why


@pytest.mark.parametrize('prefix, expression', [
    ('import os\nos.path.join = unknown\n', "os.path.join('json')"),
    ('import os\nos = unknown\n', "os.path.join('json')"),
    ('from pathlib import Path\nPath = unknown\n', "Path('json')"),
    ('def str(x): return unknown(x)\n', "str('json')"),
    ('from unknown import *\n', "str('json')"),
    ('', "Path('json')"),
])
def test_unproven_or_rebound_path_binding_keeps_overlap(monkeypatch, prefix, expression):
    tip = {'tests/__init__.py': '', SUPPORT: 'VALUE = 1\n',
           'tests/test_x.py': prefix + f'import importlib\nimportlib.import_module({expression})\n'}
    why = changed(tip, monkeypatch)
    assert why and 'shared ~ tests/placement_support.py' in why


@pytest.mark.parametrize('prefix, expression', [
    ('import os\n', "os.path.join('scripts', 'other.py')"),
    ('import os.path as paths\n', "paths.join('scripts', 'other.py')"),
    ('from os.path import join as path_join\n', "path_join('scripts', 'other.py')"),
    ('from pathlib import Path as P\n', "str(P(__file__).parent / 'other.py')"),
])
def test_even_import_bound_path_calls_stay_unknown(monkeypatch, prefix, expression):
    tip = {'tests/__init__.py': '', SUPPORT: 'VALUE = 1\n',
           'tests/test_x.py': prefix + f'import runpy\nrunpy.run_path({expression})\n'}
    why = changed(tip, monkeypatch)
    assert why and 'shared ~ tests/placement_support.py' in why

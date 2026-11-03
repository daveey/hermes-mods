"""Locate core source without running the application's installer/launcher."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest


def core_path():
    explicit = os.environ.get('HERMES_TEST_CORE')
    if explicit:
        core = Path(explicit).expanduser().resolve()
    else:
        spec = importlib.util.find_spec('hermes_constants')
        core = (Path(spec.origin).parent if spec and spec.origin
                else Path.home() / '.hermes' / 'hermes-agent')
    if not (core / 'agent').is_dir() or not (core / 'hermes_constants.py').is_file():
        raise ImportError(f'Hermes test core not found at {core}. Set HERMES_TEST_CORE '
                          'to a core checkout, or put core on PYTHONPATH; use a Python '
                          'environment with core dependencies and pytest installed.')
    return core


def require_core():
    """core_path(), or skip the calling test (or, at import time, the whole module).

    Core is private, so the public plugin-suites CI has none: core-backed modules skip
    there with the not-found reason and the core-free modules still run. A core someone
    asked for (HERMES_TEST_CORE, or test-all's HERMES_CORE_CHECKOUT) that cannot be
    resolved is a misconfiguration and still fails loudly, never a skip.
    """
    try:
        return core_path()
    except ImportError as missing:
        if os.environ.get('HERMES_TEST_CORE') or os.environ.get('HERMES_CORE_CHECKOUT'):
            raise
        # pytest and unittest both report SkipTest raised while importing a module as a skip.
        raise unittest.SkipTest(str(missing)) from None


def use_core():
    # Core entry-point modules may import bootstrap transitively. Its supported
    # no-install guard keeps this test interpreter (and its pytest) in charge.
    os.environ['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
    core = require_core()
    sys.path.insert(0, str(core))
    return core

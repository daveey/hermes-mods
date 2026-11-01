"""Locate core source without running the application's installer/launcher."""
import importlib.util
import os
from pathlib import Path
import sys


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


def use_core():
    # Core entry-point modules may import bootstrap transitively. Its supported
    # no-install guard keeps this test interpreter (and its pytest) in charge.
    os.environ['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
    core = core_path()
    sys.path.insert(0, str(core))
    return core

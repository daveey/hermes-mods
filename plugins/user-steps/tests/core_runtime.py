"""Gate for the tests that drive core's tui_gateway runtime.

Importing ``tui_gateway.server`` loads core's ``hermes_bootstrap``. Against a self-updating
install (``~/.hermes/hermes-agent``, the conftest default) from any other interpreter,
bootstrap's ``prepare_launch`` may finish a pending source update and then ``os.execv`` the
process into that install's runtime, which has no pytest. Under pytest's fd capture the
child's traceback is lost, so the whole run exits 1 with no output, and nothing under test
is isolated from the live home. These tests also need core's ``tests/conftest.py`` (home
I/O and mutation guards, the ``owner`` fixture's environment).

So they run only where core's test conftest is the active plugin, which is what
``run-core-integration.sh`` sets up (``-p tests.conftest`` in an isolated core worktree).
Everywhere else they skip with that instruction, before any core import.
"""
import sys
from pathlib import Path

import pytest

RUNNER = 'bash plugins/user-steps/run-core-integration.sh <isolated core worktree>'


def require_isolated_core():
    """Skip the calling module unless core's isolated test conftest is loaded; else import core."""
    plugin = sys.modules.get('tests.conftest')
    plugin_file = Path(getattr(plugin, '__file__', '') or '')
    core = plugin_file.parent.parent if plugin_file.name == 'conftest.py' else None
    if core is None or not (core / 'tui_gateway' / 'server.py').is_file():
        pytest.skip(f'real-core runtime test: run via {RUNNER} (needs core tests.conftest '
                    'guards; importing tui_gateway here could re-exec this process)',
                    allow_module_level=True)
    server = pytest.importorskip('tui_gateway.server')
    if not Path(server.__file__).resolve().is_relative_to(core.resolve()):
        pytest.fail(f'tui_gateway imported from {server.__file__}, not the guarded core {core}',
                    pytrace=False)
    return server

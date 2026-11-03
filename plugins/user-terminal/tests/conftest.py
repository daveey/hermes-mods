"""Core-backed tests skip, with a stated reason, when no Hermes core is importable.

plugin-suites CI (.github/workflows/plugin-suites.yml) runs this suite with no core checkout, so
the watchdog's ``_kanban_api()`` raises "cannot locate Hermes core" and ``hermes_cli``/``gateway``/
``hermes_state_*`` imports fail. A module (``pytestmark = pytest.mark.requires_core``) or test
marked ``requires_core`` is skipped there; with core on the path (scripts/test-all on the hub) the
same tests run unchanged, and a broken core still fails them. Unmarked tests always run.
"""
import importlib.util
import os

import pytest

CORE_MODULE = "hermes_cli"


def core_importable() -> bool:
    try:
        return importlib.util.find_spec(CORE_MODULE) is not None
    except (ImportError, ValueError):
        return False


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requires_core: needs an importable Hermes core (hermes_cli); skipped without one")


def pytest_collection_modifyitems(config, items):
    # A named core (scripts/test-all exports the one it selected) that does not import is a broken
    # setup, not an absent core: the tests run and fail loudly instead of skipping.
    if core_importable() or os.environ.get("HERMES_CORE_CHECKOUT"):
        return
    skip = pytest.mark.skip(reason=f"Hermes core ({CORE_MODULE}) not importable: core-backed test "
                                   "(scripts/test-all with a core checkout runs it)")
    for item in items:
        if item.get_closest_marker("requires_core"):
            item.add_marker(skip)

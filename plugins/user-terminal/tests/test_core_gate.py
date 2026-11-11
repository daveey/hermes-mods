"""With a Hermes core named or importable, no core-backed test carries a skip mark.

conftest.py skips ``requires_core`` tests only when no core is available (public plugin-suites CI). A gate that
skipped them anyway still left the suite green, with about 200 fewer tests run: only a passed-count comparison
noticed. This test fails instead. It flags any ``skip`` mark on a ``requires_core`` test, whatever its reason, so a
gate whose reason text was reworded can't slip past it either (to disable one core-backed test, use ``skipif`` or
drop its marker, not ``skip``). It reads the whole session's items, so it guards a whole-suite run, not a run
narrowed to this file.
"""
import importlib.util
import os

import pytest


def _core_available() -> bool:
    if os.environ.get("HERMES_CORE_CHECKOUT"):
        return True
    try:
        return importlib.util.find_spec("hermes_cli") is not None
    except (ImportError, ValueError):
        return False


def test_core_backed_tests_run_when_a_core_is_available(request):
    if not _core_available():
        pytest.skip("no Hermes core named or importable: the gate is meant to skip core-backed tests here")
    gated = [item.nodeid for item in request.session.items
             if item.get_closest_marker("requires_core") and item.get_closest_marker("skip")]
    assert not gated, (f"a Hermes core is available, yet {len(gated)} core-backed test(s) carry a skip mark "
                       f"(the conftest gate's, or another), e.g. {gated[:3]}")

"""platforms(...) marks gate coach tests even when coach's conftest is the only one loaded.

scripts/test-all runs this suite with its own rootdir, so core's conftest (which applies the
gating) never loads; before coach's conftest applied core's gate, Linux-only tests ran and
failed on macOS.
"""
import sys

import pytest


@pytest.mark.platforms("windows")
def test_windows_only_mark_runs_only_on_windows():
    assert sys.platform == "win32"

"""Run this plugin's node:test suites (`tests/*.test.mjs`) under pytest, so `scripts/test-all -k
session-mentions` covers the Desktop plugin too; before this, only `node --test` by hand ran them.
`live-*.mjs` probes need a real Desktop/gateway and are not included."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _node() -> str | None:
    for cand in (os.environ.get("HERMES_MODS_NODE"), shutil.which("node")):
        if not cand:
            continue
        try:
            major = int(subprocess.run([cand, "-p", "process.versions.node.split('.')[0]"], capture_output=True,
                                       text=True, timeout=20).stdout.strip())
        except Exception:
            continue
        if major >= 22:  # node:test + data: imports of ESM
            return cand
    return None


@pytest.mark.parametrize("suite", sorted(p.name for p in _HERE.glob("*.test.mjs")))
def test_node_suite(suite):
    node = _node()
    if not node:
        pytest.skip("node >= 22 not found (set HERMES_MODS_NODE)")
    run = subprocess.run([node, "--test", str(_HERE / suite)], capture_output=True, text=True, timeout=300, cwd=_HERE)
    assert run.returncode == 0, (run.stdout[-4000:] + run.stderr[-2000:])

"""Serialize actual persisted CoreReaders -> reducer output for the DOM tests."""
import contextlib
import json
import os
from pathlib import Path
import sys

core = os.environ.get('HERMES_CORE_SOURCE')
if core:
    sys.path.insert(0, core)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'plugins/session-panel/tests'))
from actionability_fixtures import dom_cases
with contextlib.redirect_stdout(sys.stderr):
    cases = dom_cases() if core else {}
print(json.dumps(cases))

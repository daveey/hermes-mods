"""Keep import-time core defaults in test-owned state, never the caller's home."""
import os
import tempfile

_state = tempfile.TemporaryDirectory(prefix='account-usage-state-')
_previous_home = os.environ.get('HERMES_HOME')
os.environ['HERMES_HOME'] = _state.name


def pytest_unconfigure(config):
    if _previous_home is None:
        os.environ.pop('HERMES_HOME', None)
    else:
        os.environ['HERMES_HOME'] = _previous_home
    _state.cleanup()

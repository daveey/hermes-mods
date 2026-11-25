"""Put the Hermes core checkout on sys.path so the real-core reader tests run instead of skipping.

HERMES_CORE_DIR overrides the default ~/.hermes/hermes-agent (core needs ruamel.yaml: `--with ruamel.yaml`)."""
import os
import sys
from pathlib import Path

try:
    import hermes_cli  # noqa: F401
except ImportError:
    _root = Path(os.environ.get('HERMES_CORE_DIR') or Path.home() / '.hermes' / 'hermes-agent')
    if (_root / 'hermes_cli' / 'kanban_db.py').is_file():
        sys.path.insert(0, str(_root))


import tempfile

# The feed saves its snapshot under HERMES_HOME, also from an atexit hook after every test has finished: the whole
# test process points it at a throwaway directory, never the real Hermes home.
os.environ['HERMES_HOME'] = tempfile.mkdtemp(prefix='session-panel-test-home-')


import pytest


@pytest.fixture(autouse=True)
def _own_hermes_home(tmp_path, monkeypatch):
    """Each test its own HERMES_HOME (snapshots never leak from one test into the next)."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes-home'))


@pytest.fixture(autouse=True)
def _supported_legacy_imports(request, _own_hermes_home):
    """Legacy import tests impersonate a serve; enroll that actual test incarnation.

    Lifecycle refusal and real-exec tests own their enrollment independently. No fake
    COMPLETE cut, mocked bind or production pytest escape is supplied here.
    """
    if request.module.__name__ not in {'test_external_panel_ownership', 'test_producer'}:
        return
    try:
        from hermes_cli.serve_producer_admission import enroll_launch
        from hermes_cli._startup_fast import project_root_str
    except ImportError:
        return
    root = Path(os.environ['HERMES_HOME'])
    enroll_launch(root / 'serve-producer-v1', 'serve', installation=Path(project_root_str()))


@pytest.fixture(autouse=True)
def _mounted_panels_for_existing_lane_scenarios(request, monkeypatch):
    """Existing lane tests describe visible chats; sidebar-only coverage lives separately."""
    if request.module.__name__ not in {
        'test_board_lane', 'test_board_lane_core', 'test_board_coalesce',
        'test_replica_pull_capture',
    }:
        return
    from test_feed import feedmod
    original = feedmod.BoardLane.__init__
    def mounted(lane, feed, *args, **kwargs):
        feed._panel_subscribers.update({s: (s, float('inf')) for s in feed.interest})
        original(lane, feed, *args, **kwargs)
    monkeypatch.setattr(feedmod.BoardLane, '__init__', mounted)


@pytest.fixture(autouse=True)
def _stop_mode_watchers(monkeypatch):
    """A lifecycle's OFF-mode watcher is a daemon thread that reads ambient home; one a test forgot to stop would
    run inside a later test's armed ambient-resolver tripwires. Every watcher started in a test ends with it."""
    try:
        from hermes_cli import serve_producer_lifecycle as lifecycle
    except ImportError:
        yield
        return
    started = []
    servers = []
    try:
        from hermes_cli import serve_producer_retirement as retirement
    except ImportError:
        retirement = None
    if retirement is not None:
        bind = retirement.bind_panel
        def record_bind(*args, **kwargs):
            server = bind(*args, **kwargs)
            if server not in servers:
                servers.append(server)
            return server
        monkeypatch.setattr(retirement, 'bind_panel', record_bind)
    original = lifecycle.PanelLifecycle.start_watching

    def recording(self, *args, **kwargs):
        started.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(lifecycle.PanelLifecycle, 'start_watching', recording)
    yield
    for item in started:
        item.stop_watching()
    for server in servers:
        assert server.participant.retire(server.drain), 'test left active panel lanes'
        server.close()
    monkeypatch.undo()  # the test's own patches first: they may have created the loader attributes popped below
    loader = sys.modules.get('_session_panel_feed_loader')
    if loader is not None:
        loader.__dict__.pop('panel_participant', None)
        loader.__dict__.pop('panel_retirement', None)
        loader.__dict__.pop('panel_lifecycle', None)
        loader.__dict__.pop('legacy_fenced', None)

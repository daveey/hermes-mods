"""A watcher a test starts is stopped with the test: no daemon thread leaks into a later tripwire window."""
import threading

import pytest

lifecycle = pytest.importorskip('hermes_cli.serve_producer_lifecycle')
_STARTED = []


def _watchers():
    return [t for t in threading.enumerate() if t.name == 'panel-producer-mode-watch' and t.is_alive()]


def test_a_started_watcher_is_recorded_and_runs(tmp_path):
    life = lifecycle.PanelLifecycle(tmp_path, admit=None, retire_legacy=None, start_legacy=None, make_relay=None,
                                    publish_initial=None, attach=None, detach=None, environ={}, legacy_running=True)
    life.poll_once = lambda: None
    life.start_watching(interval=0.05)
    _STARTED.append(life)
    assert life._thread in _watchers()


def test_the_previous_tests_watcher_was_stopped_by_the_fixture():
    assert _STARTED and _STARTED[0]._stop.is_set()
    assert _STARTED[0]._thread not in _watchers()

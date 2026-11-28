"""Unmasked I/O/readers controls; real watch integration lives in source coverage."""
import importlib.util
from pathlib import Path
import sys
import threading
import types

import pytest


def api_module():
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
    spec = importlib.util.spec_from_file_location('_panel_io_guard_controls', path)
    assert spec is not None and spec.loader is not None
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    return api


def test_inotify_no_ready_event_never_reads_descriptor(monkeypatch):
    import select
    api = api_module()
    calls = []
    poller = types.SimpleNamespace(
        register=lambda fd, events: calls.append(('register', fd, events)),
        poll=lambda timeout: calls.append(('poll', timeout)) or [])
    monkeypatch.setattr(select, 'poll', lambda: poller)
    def forbidden_read(*args):
        pytest.fail('read attempted without a ready inotify event')
    monkeypatch.setattr(api.os, 'read', forbidden_read)
    watch = object.__new__(api.FileWatch)
    watch._kq = None
    watch._fd = 12345
    assert watch.wait(0) is False
    assert calls == [('register', 12345, select.POLLIN), ('poll', 0.0)]


@pytest.mark.parametrize('explicit', [False, True])
def test_board_loop_reader_factory_binds_optional_explicit_scope(monkeypatch, explicit):
    api = api_module()
    context, installation = object(), object()
    reader = types.SimpleNamespace(home_context=context, installation=installation) if explicit else None
    monkeypatch.setattr(api, '_explicit_readers', lambda feed: reader)
    constructed, lifecycle = [], []
    result = object()
    def construct(**kwargs):
        constructed.append(kwargs)
        return result
    monkeypatch.setattr(api, 'CoreReaders', construct)
    feed = types.SimpleNamespace(stopped=threading.Event())
    feed.stopped.set()
    class Lane:
        def __init__(self, actual_feed, factory):
            assert actual_feed is feed
            assert factory() is result
        def start_service(self):
            lifecycle.append('start')
        def close(self):
            lifecycle.append('close')
    api._run_board(feed, Lane)
    assert constructed == [{'home_context': context, 'installation': installation} if explicit else {}]
    assert lifecycle == ['start', 'close']

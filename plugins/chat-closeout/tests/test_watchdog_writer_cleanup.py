"""A transition's off-thread writer must finish before either real-board fixture cleans up."""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('fixture_name', ['incident', 'real_board'])
def test_transition_writer_joins_before_home_cleanup(monkeypatch, fixture_name):
    refresh = pytest.importorskip('hermes_cli.kanban_outcome_refresh')
    if fixture_name == 'incident':
        from test_watchdog_resolution import incident as fixture
    else:
        from test_stuck_watchdog import real_board as fixture
    release = threading.Event()
    entered = threading.Event()
    threads, observed = [], []
    original_start = threading.Thread.start
    original_join = threading.Thread.join

    def start(thread, *args, **kwargs):
        if thread.name == 'card-transition-outcome':
            threads.append(thread)
        return original_start(thread, *args, **kwargs)

    def join(thread, *args, **kwargs):
        if thread in threads:
            release.set()  # deterministic: the producer cannot finish until fixture teardown joins it
        return original_join(thread, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, 'start', start)
    monkeypatch.setattr(threading.Thread, 'join', join)
    monkeypatch.setattr(refresh, 'owns_chat_sessions', lambda: True)
    monkeypatch.setattr(refresh, 'linked_chats', lambda *args: ['fixture-chat'])
    import os
    def write(chats):
        entered.set()
        assert release.wait(10), 'fixture did not join its transition writer'
        observed.append((home.is_dir(), os.environ.get('HERMES_HOME') == str(home)))
        if home.is_dir():
            (home / 'writer-completed').write_text('done')

    monkeypatch.setattr(refresh, 'refresh_chats', write)
    generator = fixture.__wrapped__(monkeypatch)
    try:
        next(generator)
        home = Path(os.environ['HERMES_HOME'])
        refresh.after_transition(SimpleNamespace(in_transaction=False), 'fixture-task')
        assert entered.wait(10)
        assert len(threads) == 1
        with pytest.raises(StopIteration):
            next(generator)
        assert not threads[0].is_alive(), 'fixture removed home before its writer finished'
        assert observed == [(True, True)]
        assert not home.exists()
    finally:
        release.set()
        for thread in threads:
            original_join(thread, 10)
        generator.close()

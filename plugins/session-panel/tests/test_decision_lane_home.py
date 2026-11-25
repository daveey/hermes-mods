"""Decision-lane startup retains explicit home even before its first scheduled refresh."""
from types import SimpleNamespace
import threading

import pytest
import test_explicit_home_acquisition as acquisition

api = acquisition.api


@pytest.mark.parametrize('explicit', [False, True])
def test_decision_lane_constructs_home_bound_readers(api, monkeypatch, explicit):
    context = object() if explicit else None
    calls, releases = [], []
    readers = SimpleNamespace(home_context=context, installation='installation')
    feed = SimpleNamespace(readers=readers, stopped=threading.Event())
    feed.stopped.set()

    def factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(release=lambda: releases.append(True))

    monkeypatch.setattr(api, 'CoreReaders', factory)
    api._run_decisions(feed)
    assert calls == ([{'home_context': context, 'installation': 'installation'}] if explicit else [{}])
    assert releases == [True]

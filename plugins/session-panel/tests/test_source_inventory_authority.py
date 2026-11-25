"""Healthy inventory loss must revoke authority, not only retry deadlines."""
import time

import pytest

from test_core_readers import api, board
from test_source_coordinator_core import compose, drain, readers_for, strict_empty, kanban_db
from test_source_reload_progress import install, settle
from test_feed import feedmod


@pytest.mark.parametrize('route', ['keyed', 'full'])
@pytest.mark.parametrize('mode', ['removed', 'replaced', 'unchanged'])
@pytest.mark.parametrize('reloads', [1, 2])
def test_healthy_inventory_authority_on_reload(api, board, monkeypatch, route, mode, reloads):
    path, conn = board
    readers = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(readers, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='historical source row', session_id='S')
    now = [time.time()]
    feed = feedmod.Feed(readers, emit=lambda _: None, clock=lambda: now[0])
    feed.watch(['S'])
    owner = compose(feed, readers)
    feeds, owners = [feed], [owner]
    replacement = None
    try:
        feed.changes(['S']); drain(owner)
        removed = owner.sources[0]
        before = owner.store.pin(owner.chats['S'])[1]
        assert removed in before.required_versions and not before.missing_scopes
        prior_version = before.required_versions[removed]
        prior_attempt = (owner.attempts[removed].count, owner.attempts[removed].retry_at)
        if mode == 'removed':
            monkeypatch.setattr(readers, 'boards', lambda: [])
        elif mode == 'replaced':
            from hermes_cli.kanban_db_connect import connect
            new_path = path.parent / 'replacement.db'
            replacement = connect(new_path)
            kanban_db.create_task(replacement, title='replacement source row', session_id='S')
            monkeypatch.setattr(readers, 'boards', lambda: [('default', new_path)])
        for generation in range(reloads):
            feed, owner = install(feed, readers, now, monkeypatch)
            feeds.append(feed); owners.append(owner)
            admission = owner.store.pin(owner.chats['S'])[1]
            if mode == 'unchanged':
                assert admission.required_versions[removed] == prior_version
                assert (owner.attempts[removed].count, owner.attempts[removed].retry_at) == prior_attempt
            else:
                assert removed not in admission.required_versions, 'inventory loss retained healthy authority'
                assert removed in admission.missing_scopes
                assert removed not in owner.attempts and removed not in owner.scope_attempts
                if mode == 'replaced':
                    assert owner.sources[0] != removed
                    if generation == 0:
                        assert owner.sources[0] in admission.missing_scopes
                    else:
                        assert owner.sources[0] in admission.required_versions
            if route == 'keyed':
                feed.queue_keyed(['S']); settle(owner, now); feed.refresh_keyed()
            else:
                settle(owner, now); feed._publish(owner.full(['S']))
            now[0] = time.time()
            held = feed.bundles['S']
            projection = held['board_projection']
            titles = {row['title'] for row in held['bundle']['tasks']}
            assert 'historical source row' in titles
            assert owner.store.current(projection.admission)
            if mode == 'unchanged':
                assert removed in projection.covered_scopes
                assert not held.get('source_stale') and feed._bundle_fresh('S', now[0])
            else:
                assert removed not in projection.covered_scopes
                assert removed in projection.missing_scopes
                assert held.get('source_stale') is True
                assert not feed._bundle_fresh('S', now[0])
                if mode == 'replaced':
                    assert 'replacement source row' in titles
                    assert owner.sources[0] in projection.covered_scopes
            # Canonical refresh/full publication can enqueue its own successor
            # projection; consume that real completion before asserting idle.
            settle(owner, now)
            assert not owner.pending and not owner.active and not owner.acquiring
            assert owner.retry_at() is None
    finally:
        for feed, owner in zip(feeds, owners):
            feed.stopped.set(); owner.close()
        readers.release()
        if replacement is not None:
            replacement.close()

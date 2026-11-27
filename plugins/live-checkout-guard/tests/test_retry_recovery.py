"""A3 catch-up regressions: real publication, isolated transport faults and HOME."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
from test_source_review import repository, pub, plugin


def record(root, name):
    result = pub.defer(root, [name], spawn=False)
    path = Path(result['record'])
    return path.parent, path, path.read_bytes()


@pytest.mark.parametrize('fault', ['timeout', 'budget', 'race', 'unknown'])
def test_transient_attempt_keeps_record_then_publishes(repository, monkeypatch, fault):
    root, origin, name, target, marker, head, git = repository
    directory, path, raw = record(root, name)
    original = pub.bounded
    count = [0]
    racer = root.parent / 'racer'
    if fault == 'race':
        git('clone', '-q', str(origin), str(racer), cwd=root.parent)
    def transport(command, **kwargs):
        if 'push' in command:
            count[0] += 1
            if fault == 'timeout':
                # A genuinely slow trusted transport boundary, never fetched hooks.
                return original([sys.executable, '-c', 'import time; time.sleep(10)'],
                                timeout=0.03)
            if fault == 'budget':
                with pub.operation_budget(-1, pub.time.monotonic):
                    pub.remaining(30)
            if fault == 'race':
                git('commit', '--allow-empty', '-qm', 'main race', cwd=racer)
                git('push', '-q', 'origin', 'HEAD:main', cwd=racer)
                return original(command, **kwargs)  # real non-fast-forward refusal
            raise OSError('fixture transport temporarily unavailable')
        return original(command, **kwargs)
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}, create=True):
        with patch.object(pub, 'bounded', side_effect=transport):
            counts = pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)
        assert count[0] > 0
        assert path.exists(), 'transient failure dropped pending edit'
        assert path.read_bytes() == raw, 'retry extended the original deadline'
        assert counts['kept'] == 1 and counts['dropped'] == 0
        assert git('show', 'main:' + name, cwd=origin) == 'base'
        if fault == 'race':
            assert count[0] == 4 and git('rev-parse', 'main', cwd=origin) != head
        else:
            assert git('rev-parse', 'main', cwd=origin) == head
        assert target.read_text() == 'edited\n'
        assert git('worktree', 'list', '--porcelain').count('worktree ') == 1
        counts = pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)
    assert counts['published'] == 1 and not path.exists()
    assert git('show', 'main:' + name, cwd=origin) == 'edited'
    assert git('status', '--porcelain') == ''
    # Legacy RED runs may execute their fetched hooks. Fixed code must not.
    assert not marker.exists()


@pytest.mark.parametrize('reason', ['overlap', 'unknown', 'ci-red', 'ci-unknown'])
def test_holds_remain_pending_and_visible(repository, reason, caplog):
    root, origin, name, target, marker, head, git = repository
    directory, path, raw = record(root, name)
    state = reason.removeprefix('ci-') if reason.startswith('ci-') else 'green'
    overlap = {'status': reason if state == 'green' else 'clear'}
    with patch.object(pub, 'publication_overlap', return_value=overlap, create=True):
        counts = pub.retry_pending(directory, ci=lambda *_: state, passes=1)
    assert path.exists(), 'a held edit must remain retryable until deadline'
    assert path.read_bytes() == raw
    assert counts['kept'] == 1
    log = (directory.parent.parent / 'logs/skill-publish-retry.log').read_text()
    assert 'held' in log and name in log
    assert 'skill publication dirt' in caplog.text and name in caplog.text
    assert git('rev-parse', 'main', cwd=origin) == head


@pytest.mark.parametrize('refusal', ['staged', 'scope', 'privacy', 'expiry', 'malformed'])
def test_terminal_records_drop_with_dirt_notice(repository, refusal, caplog):
    root, origin, name, target, marker, head, git = repository
    if refusal == 'scope':
        script = root / 'skills/example/tests/test_fixture.py'
        script.parent.mkdir()
        script.write_text('raise RuntimeError("never execute")\n')
        git('add', str(script))
        git('commit', '-qm', 'fixture suite')
        git('push', '-q', 'origin', 'HEAD:main')
    directory, path, raw = record(root, name)
    if refusal == 'staged':
        git('add', name)
    elif refusal == 'privacy':
        target.write_text('PRIVATE_FIXTURE_MARKER\n')
    elif refusal == 'malformed':
        path.write_text(json.dumps({'root': str(root), 'paths': [name], 'deadline': 'bad'}))
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}, create=True):
        counts = pub.retry_pending(directory, ci=lambda *_: 'green', passes=1,
                                   clock=(lambda: float('inf')) if refusal == 'expiry' else None)
    assert not path.exists()
    assert counts['expired' if refusal == 'expiry' else 'dropped'] == 1
    assert target.exists() and git('status', '--porcelain')
    assert 'skill publication dirt' in caplog.text and name in caplog.text
    log = (directory.parent.parent / 'logs/skill-publish-retry.log').read_text()
    assert ('expired' if refusal == 'expiry' else 'dropped') in log


def test_held_suite_slot_does_not_block_document_publication(repository, monkeypatch):
    root, origin, name, target, marker, head, git = repository
    slot = root.parent / '.cache/hermes-mods-push/slots/0.lock'
    slot.parent.mkdir(parents=True)
    with slot.open('w') as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # No suite receipt and a real held host slot. Fetched hook is a trap too.
        hook = root / '.githooks/pre-push'
        hook.write_text('#!/bin/sh\nprintf fetched > ' + str(marker) + '\nexit 1\n')
        git('add', '.githooks/pre-push')
        git('commit', '-qm', 'fetched suite trap')
        git('push', '-q', 'origin', 'HEAD:main')
        with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}, create=True):
            result = pub.publish(root, [name], ci=lambda *_: 'green')
    assert result['status'] == 'published'
    assert not marker.exists(), 'fetched suite hook ran instead of lightweight plugin hook'
    assert git('status', '--porcelain') == ''


@pytest.mark.parametrize('tool', ['skill_manage', 'terminal'])
def test_observer_recovers_dirty_skill_when_pending_directory_empty(repository, monkeypatch, tool):
    root, origin, name, target, marker, head, git = repository
    monkeypatch.setenv('LIVE_CHECKOUT_ROOT', str(root))
    monkeypatch.setattr(plugin, '_load_sibling', lambda _: pub)
    directory = pub.pending_dir()
    assert not list(directory.glob('*.json'))
    with patch.object(pub, 'spawn_worker') as spawn:
        plugin._post_tool_call(tool, {}, {'success': False})
        assert spawn.called, 'orphan dirty skill did not restart publisher'
    paths = [json.loads(p.read_text())['paths'] for p in directory.glob('*.json')]
    assert paths == [[name]]
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}, create=True):
        assert pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)['published'] == 1
    assert git('status', '--porcelain') == ''


def test_observer_timeout_is_deferred_and_visible(repository, monkeypatch, caplog):
    root, origin, name, target, marker, head, git = repository
    monkeypatch.setenv('LIVE_CHECKOUT_ROOT', str(root))
    monkeypatch.setattr(plugin, '_load_sibling', lambda _: pub)
    with patch.object(pub, 'publish', side_effect=subprocess.TimeoutExpired('fixture push', 0.1)), \
         patch.object(pub, 'spawn_worker'):
        plugin._post_tool_call('skill_manage', {'action': 'patch'},
                               {'success': True, 'path': str(target)})
    assert list(pub.pending_dir().glob('*.json')), 'observer lost timed-out first attempt'
    assert name in caplog.text and 'skill publication dirt' in caplog.text


def test_kick_respects_worker_lock_and_existing_deadline(repository):
    root, origin, name, target, marker, head, git = repository
    directory, path, raw = record(root, name)
    with (directory / '.worker.lock').open('w') as lock, patch.object(pub, 'spawn_worker') as spawn:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert not pub.kick(directory, root=root)
        assert not spawn.called
        assert path.read_bytes() == raw
        fcntl.flock(lock, fcntl.LOCK_UN)
        assert pub.kick(directory, root=root)
        assert spawn.call_count == 1
        assert path.read_bytes() == raw


@pytest.mark.parametrize('reason', ['expiry', 'staged', 'privacy'])
def test_recovery_does_not_renew_settled_bytes_but_accepts_new_edit(repository, reason):
    root, origin, name, target, marker, head, git = repository
    if reason == 'privacy':
        target.write_text('PRIVATE_FIXTURE_MARKER\n')
    if reason == 'staged':
        git('add', name)
    directory, path, raw = record(root, name)
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        counts = pub.retry_pending(directory, ci=lambda *_: 'green', passes=1,
                                   clock=(lambda: float('inf')) if reason == 'expiry' else None)
    assert counts['expired' if reason == 'expiry' else 'dropped'] == 1
    with patch.object(pub, 'spawn_worker') as spawn:
        assert not pub.kick(directory, root=root)
        assert not spawn.called
        assert not path.exists()
        if reason == 'staged':
            git('reset', '-q', 'HEAD', '--', name)
        target.write_text('a newer safe edit\n')
        assert pub.kick(directory, root=root)
        assert spawn.called and path.exists()
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        assert pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)['published'] == 1
    assert git('status', '--porcelain') == ''


def test_unknown_push_result_after_remote_success_is_reconciled_on_retry(repository):
    root, origin, name, target, marker, head, git = repository
    directory, path, raw = record(root, name)
    original = pub.bounded
    outage = [False]
    def transport(command, **kwargs):
        if outage[0] and 'fetch' in command:
            raise subprocess.TimeoutExpired(command, 0.1)
        result = original(command, **kwargs)
        if 'push' in command and result.returncode == 0:
            outage[0] = True
            raise subprocess.TimeoutExpired(command, 0.1)
        return result
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        with patch.object(pub, 'bounded', side_effect=transport):
            assert pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)['kept'] == 1
        assert path.read_bytes() == raw
        assert git('show', 'main:' + name, cwd=origin) == 'edited'
        # The remote did accept it: retry must not duplicate or lose the edit.
        remote = git('rev-parse', 'main', cwd=origin)
        assert pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)['published'] == 1
        assert git('rev-parse', 'main', cwd=origin) == remote
    assert git('status', '--porcelain') == ''


@pytest.mark.parametrize('deadline', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_deadline_is_malformed(repository, deadline):
    root, origin, name, target, marker, head, git = repository
    directory, path, raw = record(root, name)
    rec = json.loads(raw)
    rec['deadline'] = deadline
    path.write_text(json.dumps(rec))
    assert pub.retry_pending(directory, passes=1)['dropped'] == 1
    assert not path.exists()


def test_discovery_refuses_script_path_with_visible_notice(repository, caplog):
    root, origin, name, target, marker, head, git = repository
    script = root / 'skills/example/scripts/helper.py'
    script.parent.mkdir()
    script.write_text('raise RuntimeError("do not execute")\n')
    target.write_text('base\n')
    with patch.object(pub, 'spawn_worker') as spawn:
        assert not pub.kick(root=root)
        assert not spawn.called
    assert 'scripts/helper.py' in caplog.text and 'refused' in caplog.text


def test_expired_record_does_not_settle_newer_unattempted_bytes(repository):
    root, origin, name, target, marker, head, git = repository
    directory, path, raw = record(root, name)
    target.write_text('new generation never attempted\n')
    assert pub.retry_pending(directory, clock=lambda: float('inf'), passes=1)['expired'] == 1
    with patch.object(pub, 'spawn_worker') as spawn:
        assert pub.kick(directory, root=root), 'old deadline settled a new unattempted edit'
        assert spawn.called and path.exists()
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        assert pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)['published'] == 1
    assert git('show', 'main:' + name, cwd=origin) == 'new generation never attempted'


@pytest.mark.parametrize('malformed', [[], None, {'root': 'other', 'paths': None}])
def test_kick_ignores_malformed_record_and_starts_repair_worker(repository, malformed):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    directory.mkdir(parents=True)
    (directory / 'malformed.json').write_text(json.dumps(malformed))
    with patch.object(pub, 'spawn_worker') as spawn:
        assert pub.kick(directory, root=root)
        assert spawn.called
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        counts = pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)
    assert counts['published'] == 1 and counts['dropped'] == 1
    assert git('status', '--porcelain') == ''


def test_busy_worker_does_not_hide_unrecorded_orphan(repository):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    directory.mkdir(parents=True)
    with (directory / '.worker.lock').open('w') as lock, patch.object(pub, 'spawn_worker') as spawn:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert pub.kick(directory, root=root)
        assert spawn.called  # safe handoff if incumbent exits during discovery
        assert [json.loads(p.read_text())['paths'] for p in directory.glob('*.json')] == [[name]]
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        assert pub.retry_pending(directory, ci=lambda *_: 'green', passes=1)['published'] == 1

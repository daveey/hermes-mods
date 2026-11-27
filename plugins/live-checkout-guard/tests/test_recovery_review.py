"""Published-head recovery review regressions, isolated real Git origins."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_source_review import repository, pub, plugin


@pytest.mark.parametrize('action', ['delete', 'remove_file', 'accidental'])
def test_discovery_holds_deleted_documents(repository, monkeypatch, caplog, action):
    root, origin, name, target, marker, head, git = repository
    target.unlink()
    monkeypatch.setenv('LIVE_CHECKOUT_ROOT', str(root))
    monkeypatch.setattr(plugin, '_load_sibling', lambda _: pub)
    with patch.object(pub, 'spawn_worker') as spawn:
        plugin._post_tool_call('terminal' if action == 'accidental' else 'skill_manage',
                              {'action': action, 'name': 'example'}, {'success': True})
    assert not list(pub.pending_dir().glob('*.json')), 'deletion enrolled for publication'
    assert not spawn.called
    assert name in caplog.text and 'deletion' in caplog.text
    assert git('rev-parse', 'main', cwd=origin) == head
    assert not target.exists()


def test_pending_edit_deleted_before_retry_never_publishes_deletion(repository, caplog):
    root, origin, name, target, marker, head, git = repository
    pub.defer(root, [name], spawn=False)
    target.unlink()
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        counts = pub.retry_pending(ci=lambda *_: 'green', passes=1)
    assert counts['dropped'] == 1 and counts['published'] == 0
    assert 'deletion requires reviewed publication' in caplog.text
    assert git('rev-parse', 'main', cwd=origin) == head
    assert git('show', 'main:' + name, cwd=origin) == 'base'


@pytest.mark.parametrize('kind', ['settled', 'script', 'deletion'])
def test_discovery_notifies_once_per_generation(repository, caplog, kind):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    directory.mkdir(parents=True)
    if kind == 'settled':
        pub._settle(directory, str(root), [name], {name: pub._signature(root, name)})
    elif kind == 'script':
        target.write_text('base\n')
        target = root / 'skills/example/scripts/helper.py'
        target.parent.mkdir()
        target.write_text('first generation\n')
        name = target.relative_to(root).as_posix()
    else:
        target.unlink()
    with patch.object(pub, 'spawn_worker'):
        for _ in range(50):
            pub.kick(root=root)
    messages = [r.message for r in caplog.records if 'skill publication dirt' in r.message]
    assert len(messages) == 1, 'unchanged dirt logged on every observation'
    log = directory.parent.parent / 'logs/skill-publish-retry.log'
    assert log.read_text().count('skill publication dirt') == 1
    if kind != 'deletion':
        caplog.clear()
        target.write_text('second generation\n')
        with patch.object(pub, 'spawn_worker'):
            for _ in range(5):
                pub.kick(root=root)
        assert sum('skill publication dirt' in r.message for r in caplog.records) == 1


@pytest.mark.parametrize('refusal', ['privacy', 'staged'])
def test_direct_observer_refusal_is_not_deferred(repository, monkeypatch, caplog, refusal):
    root, origin, name, target, marker, head, git = repository
    if refusal == 'privacy':
        target.write_text('PRIVATE_FIXTURE_MARKER\n')
    else:
        git('add', name)
    monkeypatch.setenv('LIVE_CHECKOUT_ROOT', str(root))
    monkeypatch.setattr(plugin, '_load_sibling', lambda _: pub)
    publish = pub.publish
    def direct(*args, **kwargs):
        return publish(*args, ci=lambda *_: 'green', **kwargs)
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}), \
         patch.object(pub, 'publish', side_effect=direct), \
         patch.object(pub, 'spawn_worker') as spawn:
        plugin._post_tool_call('skill_manage', {'action': 'patch'},
                              {'success': True, 'path': str(target)},
                              session_id='fixture-session', task_id='fixture-card')
    assert not list(pub.pending_dir().glob('*.json'))
    assert not spawn.called
    assert 'refused' in caplog.text and 'held retryable' not in caplog.text
    assert git('rev-parse', 'main', cwd=origin) == head


def test_recovered_skill_documents_group_and_identify_recovery(repository):
    root, origin, name, target, marker, head, git = repository
    reference = root / 'skills/example/references/guide.md'
    reference.parent.mkdir()
    reference.write_text('recovered reference\n')
    with patch.object(pub, 'spawn_worker'):
        pub.kick(root=root)
    records = [json.loads(p.read_text()) for p in pub.pending_dir().glob('*.json')]
    assert len(records) == 1
    assert set(records[0]['paths']) == {name, reference.relative_to(root).as_posix()}
    assert records[0]['session_id'] == 'orphan-recovery'
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        assert pub.retry_pending(ci=lambda *_: 'green', passes=1)['published'] == 1
    assert git('rev-list', '--count', head + '..main', cwd=origin) == '1'
    assert 'session:orphan-recovery' in git('log', '-1', '--format=%s', cwd=origin)
    assert git('status', '--porcelain') == ''


@pytest.mark.parametrize('unreadable', ['directory', 'permission'])
def test_unreadable_record_is_quarantined_once(repository, monkeypatch, unreadable):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    directory.mkdir(parents=True)
    bad = directory / 'bad.json'
    if unreadable == 'directory':
        bad.mkdir()
    else:
        bad.write_text('preserve unreadable bytes')
        original = Path.read_bytes
        def read(path):
            if path == bad:
                raise PermissionError('fixture permission denied')
            return original(path)
        monkeypatch.setattr(Path, 'read_bytes', read)
    counts = pub.retry_pending(passes=3, sleep=lambda _: None)
    assert counts['dropped'] == 1
    assert not bad.exists(), 'unreadable entry keeps worker alive forever'
    quarantined = list(directory.glob('.quarantine-*'))
    assert len(quarantined) == 1
    if unreadable == 'directory':
        assert quarantined[0].is_dir()
    else:
        assert quarantined[0].read_text() == 'preserve unreadable bytes'


def test_quarantine_failure_exits_without_retry_loop(repository, monkeypatch):
    directory = pub.pending_dir()
    directory.mkdir(parents=True)
    bad = directory / 'bad.json'
    bad.mkdir()
    original = Path.rename
    def rename(path, target):
        if path == bad:
            raise PermissionError('fixture rename denied')
        return original(path, target)
    monkeypatch.setattr(Path, 'rename', rename)
    counts = pub.retry_pending(passes=3, sleep=lambda _: pytest.fail('unreadable loop'))
    assert counts['dropped'] == 1 and bad.exists()
    assert 'quarantine failed; worker exiting' in (directory.parent.parent / 'logs/skill-publish-retry.log').read_text()


def test_probe_lock_is_released_before_discovery(repository):
    root, origin, name, target, marker, head, git = repository
    pub.defer(root, [name], spawn=False)
    original = pub.run
    observed = []
    def run(*args, **kwargs):
        if 'diff' in args:
            with patch.object(pub, 'publish', return_value={'status': 'skipped', 'reason': 'fixture'}):
                observed.append(pub.retry_pending(passes=1)['busy'])
        return original(*args, **kwargs)
    with patch.object(pub, 'run', side_effect=run), patch.object(pub, 'spawn_worker'):
        pub.kick(root=root)
    assert observed == [0], 'discovery stole custody from a defer-spawned worker'

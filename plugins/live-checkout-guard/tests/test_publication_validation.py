"""New admission branches and plugin-owned hooks; external services are isolated."""
import subprocess
import urllib.request
from unittest.mock import patch

import pytest
from test_source_review import HERE, load, pub, overlap, plugin, repository


def test_overlap_failure_and_malformed_verdict_hold(repository):
    root, origin, name, target, marker, head, git = repository
    for result in [None, {}, {'status': 'unexpected'}]:
        with patch.object(pub, 'publication_overlap', return_value=result):
            assert pub.publish(root, [name], ci=lambda *_: 'green')['status'] == 'skipped'
    with patch.object(pub, 'publication_overlap', side_effect=RuntimeError('probe')):
        assert pub.publish(root, [name], ci=lambda *_: 'green')['status'] == 'skipped'
    assert git('rev-parse', 'main', cwd=origin) == head


def test_overlap_rechecked_before_push(repository):
    root, origin, name, target, marker, head, git = repository
    with patch.object(pub, 'publication_overlap', side_effect=[{'status': 'clear'}, {'status': 'overlap'}]) as check:
        result = pub.publish(root, [name], ci=lambda *_: 'green')
    assert check.call_count == 2
    assert result['status'] == 'skipped'
    assert git('rev-parse', 'main', cwd=origin) == head
    assert target.read_text() == 'edited\n'
    assert not marker.exists()


def test_publication_overlap_real_transport_binding(repository):
    mod = load('fixture_overlap_for_publisher', 'pr_overlap.py')
    with patch.object(pub.importlib.util, 'module_from_spec', return_value=mod), \
         patch.object(pub.importlib.util, 'spec_from_file_location') as spec, \
         patch.object(pub, 'run', side_effect=[b'https://github.com/example/mods.git', b'https://github.com/example/other.git']):
        spec.return_value.loader.exec_module.side_effect = lambda _: None
        with patch.object(mod, 'Checker') as checker:
            checker.return_value.check.return_value = {'status': 'clear'}
            assert pub.publication_overlap(repository[0], [repository[2]]) == {'status': 'clear'}
            assert [call.args[0] for call in checker.return_value.check.call_args_list] == ['example/mods', 'example/other']
            assert checker.call_args.kwargs['fetch'] is mod.gh_fetch
            assert checker.call_args.kwargs['list_ttl'] < 0
    assert pub.publication_overlap(repository[0], [repository[2]])['status'] == 'unknown'


def test_overlap_adapter_exception_blocks(monkeypatch):
    monkeypatch.setattr(plugin, '_skill_overlap', lambda *a: (_ for _ in ()).throw(ValueError('fixture')))
    assert plugin._pre_tool_call('skill_manage', {'action': 'patch'})['action'] == 'block'


@pytest.mark.parametrize('url', ['https://example.com/next', 'http://api.github.com/next', 'https://api.github.com:444/next'])
def test_redirect_refuses_before_forwarding_authorization(url):
    request = urllib.request.Request(overlap.API + '/start', headers={'Authorization': 'Bearer fixture'})
    with pytest.raises(overlap.Unknown):
        overlap.ApiRedirectHandler().redirect_request(request, None, 302, 'Found', {}, url)


def test_same_origin_redirect_supported():
    request = urllib.request.Request(overlap.API + '/start')
    result = overlap.ApiRedirectHandler().redirect_request(request, None, 302, 'Found', {}, overlap.API + '/next')
    assert result.full_url == overlap.API + '/next'


def test_claude_malformed_payload_fails_closed():
    result = subprocess.run(['python3', str(HERE / 'claude_hook.py')], input='{bad', text=True, capture_output=True)
    assert result.returncode == 2
    assert 'classification failed' in result.stderr


@pytest.mark.parametrize('addition', ['skills/example/tests/test_doc.py', 'skills/example/scripts/check.py', 'skills/example/guide.test.js'])
def test_fetched_skill_tests_held_not_executed(repository, addition):
    root, origin, name, target, marker, head, git = repository
    script = root / addition
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('raise RuntimeError("never execute fetched skill code")\n')
    git('add', addition)
    git('commit', '-qm', 'skill code fixture')
    git('push', '-q', 'origin', 'HEAD:main')
    before = git('rev-parse', 'HEAD')
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        with pytest.raises(ValueError, match='scripts/tests'):
            pub.publish(root, [name], ci=lambda *_: 'green')
    assert git('rev-parse', 'main', cwd=origin) == before
    assert target.read_text() == 'edited\n'
    assert not marker.exists()


def test_privacy_denylist_not_disabled(repository, monkeypatch):
    root, origin, name, target, marker, head, git = repository
    target.write_text('PRIVATE_FIXTURE_MARKER\n')
    monkeypatch.setenv('HERMES_MODS_ALLOW_UNSCANNED', '1')
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        with pytest.raises(ValueError, match='denylist'):
            pub.publish(root, [name], ci=lambda *_: 'green')
    assert git('rev-parse', 'main', cwd=origin) == head
    assert not marker.exists()


def test_missing_denylist_holds(repository, monkeypatch):
    root, origin, name, target, marker, head, git = repository
    monkeypatch.delenv('HERMES_MODS_DENYLIST')
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        with pytest.raises(ValueError, match='privacy check.*denylist'):
            pub.publish(root, [name], ci=lambda *_: 'green')
    assert git('rev-parse', 'main', cwd=origin) == head


@pytest.mark.parametrize('name,mode', [
    ('plugins/example/code.py', 'file'),
    ('skills/example/.private.md', 'file'),
    ('skills/example/not-a-skill.md', 'file'),
    ('skills/references/topic.md', 'file'),
    ('skills/example/assets/image.png', 'file'),
    ('skills/example/references/topic.md', 'executable'),
    ('skills/example/references/topic.md', 'symlink'),
])
def test_trusted_scope_rejects_non_doc_or_unscannable_entries(repository, name, mode, monkeypatch):
    root = repository[0]
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'symlink':
        p.symlink_to(repository[3])
    else:
        p.write_text('fixture\n')
        if mode == 'executable':
            p.chmod(0o755)
    repository[-1]('add', name)
    validator = load('fixture_trusted_validator', 'publication_validate.py')
    monkeypatch.chdir(root)
    with pytest.raises(ValueError):
        validator.validate_scope('HEAD')


@pytest.mark.parametrize('suppression', ['none', 'git-fingerprint', 'stdin-fingerprint', 'config-and-inline'])
def test_gitleaks_ignores_fetched_suppressions(repository, suppression):
    import secrets
    root, origin, name, target, marker, head, git = repository
    if suppression != 'none':
        (root / '.gitleaks.toml').write_text('[allowlist]\nregexes = [".*"]\n')
        fingerprint = (name if suppression == 'git-fingerprint' else 'stdin') + ':github-pat:1'
        (root / '.gitleaksignore').write_text(fingerprint + '\n')
        git('add', '.gitleaks.toml', '.gitleaksignore')
        git('commit', '-qm', 'fetched suppression fixture')
        git('push', '-q', 'origin', 'HEAD:main')
        head = git('rev-parse', 'HEAD')
        assert git('show', 'main:.gitleaksignore', cwd=origin) == fingerprint
    # Generated only inside the disposable fixture; never a real credential.
    target.write_text('token = "' + 'ghp_' + secrets.token_hex(18) + '"' +
                      (' # gitleaks:allow' if suppression == 'config-and-inline' else '') + '\n')
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        with pytest.raises(ValueError, match='gitleaks refused'):
            pub.publish(root, [name], ci=lambda *_: 'green')
    assert git('rev-parse', 'main', cwd=origin) == head
    assert not marker.exists()


@pytest.mark.parametrize('payload', ['', 'a b c d\ne f g h\n', 'refs/heads/x a refs/heads/other b\n', 'refs/heads/x a refs/heads/main 000000\n'])
def test_prepush_rejects_unvalidated_updates(payload):
    result = subprocess.run(['python3', str(HERE / 'publication_validate.py'), 'pre-push'], input=payload, text=True, capture_output=True)
    assert result.returncode == 1

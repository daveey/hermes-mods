"""Behavioral regressions for batch04 source review; no live HOME or network."""
import importlib.util
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pub = load('review_publisher', 'skill_publish.py')
overlap = load('review_overlap', 'pr_overlap.py')
plugin = load('review_adapter', '__init__.py')


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'Fixture')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'fixture@example.com')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'Fixture')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'fixture@example.com')
    deny = tmp_path / 'denylist'
    deny.write_text('PRIVATE_FIXTURE_MARKER\n')
    monkeypatch.setenv('HERMES_MODS_DENYLIST', str(deny))
    root, origin = tmp_path / 'repo', tmp_path / 'origin.git'
    def git(*args, cwd=root):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(cwd), *args], stderr=subprocess.PIPE).decode().strip()
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
    subprocess.run(['git', 'clone', '-q', str(origin), str(root)], check=True, capture_output=True)
    name = 'skills/example/SKILL.md'
    target = root / name
    target.parent.mkdir(parents=True)
    target.write_text('base\n')
    marker = tmp_path / 'executed-fetched-hook'
    for hook in ('pre-commit', 'pre-push', 'post-checkout', 'pre-rebase'):
        h = root / '.githooks' / hook
        h.parent.mkdir(exist_ok=True)
        h.write_text('#!/bin/sh\nprintf executed > ' + str(marker) + '\n')
        h.chmod(0o755)
    git('add', '.')
    git('commit', '-qm', 'base')
    git('push', '-q', 'origin', 'HEAD:main')
    git('fetch', '-q', 'origin')
    head = git('rev-parse', 'HEAD')
    target.write_text('edited\n')
    return root, origin, name, target, marker, head, git


@pytest.mark.parametrize('state', ['overlap', 'unknown'])
def test_publisher_holds_overlap_and_unknown_before_commit(repository, state):
    root, origin, name, target, marker, head, git = repository
    # The missing integration is the regression: patching a new boundary on old code
    # does not cause a setup error; old publish simply ignores it and really pushes.
    with patch.object(pub, 'publication_overlap', return_value={'status': state, 'reason': 'fixture'}, create=True) as check:
        result = pub.publish(root, [name], ci=lambda *_: 'green')
    assert result['status'] == 'skipped'
    assert state in result['reason']
    assert check.called
    assert git('rev-parse', 'main', cwd=origin) == head
    assert target.read_text() == 'edited\n'
    assert not marker.exists()


def test_publisher_never_executes_fetched_hooks(repository):
    root, origin, name, target, marker, head, git = repository
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}, create=True):
        result = pub.publish(root, [name], ci=lambda *_: 'green')
    assert not marker.exists(), 'a fetched repository hook executed'
    assert result['status'] == 'published'
    assert git('show', 'main:' + name, cwd=origin) == 'edited'


@pytest.mark.parametrize('command', [
    'COACH_ALLOW_LIVE=1 git reset --hard HEAD',
    'env COACH_ALLOW_LIVE=1 git checkout main',
    'HERMES_UPDATE_OK=1 hermes update --yes',
    "HERMES_UPDATE_OK=1 bash -c 'hermes update'",
    "HERMES_UPDATE_OK=1 bash <<'EOF'\nhermes update\nEOF",
])
def test_agent_inline_approval_cannot_authorize(command, repository, monkeypatch):
    root = repository[0]
    monkeypatch.setenv('LIVE_CORE_CHECKOUT', str(root))
    monkeypatch.setenv('LIVE_CHECKOUT_ROOT', str(root.parent / 'mods'))
    assert plugin.guard.check_shell(command, str(root))


def test_adapter_classification_exception_blocks(monkeypatch):
    monkeypatch.setattr(plugin, 'classify', lambda *a: (_ for _ in ()).throw(RecursionError('fixture')))
    result = plugin._pre_tool_call('terminal', {'command': 'echo probe'})
    assert result and result['action'] == 'block'


def test_update_depth_exhaustion_blocks():
    assert plugin.guard._check_update('hermes update', '/', depth=21)


@pytest.mark.parametrize('url', ['https://example.com/next', 'http://api.github.com/next',
                                'https://api.github.com:444/next', 'https://user@api.github.com/next'])
def test_pagination_rejects_foreign_origin_before_fetch(tmp_path, url):
    calls = []
    def fetch(where, token, timeout):
        calls.append(where)
        return [], url if len(calls) == 1 else None
    checker = overlap.Checker(fetch=fetch, token='fixture-token', cache_dir=tmp_path)
    with pytest.raises(overlap.Unknown):
        checker._pages(overlap.API + '/repos/example/repo/pulls', 3)
    assert calls == [overlap.API + '/repos/example/repo/pulls']


def test_direct_http_foreign_url_never_receives_token():
    with patch.object(overlap.urllib.request, 'urlopen') as opened:
        with pytest.raises(overlap.Unknown):
            overlap.http_fetch('https://example.com/next', 'fixture-token', 1)
    opened.assert_not_called()


@pytest.mark.parametrize('mode', ['redirect', 'link'])
def test_real_http_cross_origin_never_receives_token(tmp_path, mode):
    received = []
    class Victim(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get('Authorization'))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'[]')
        def log_message(self, *_):
            pass
    victim = ThreadingHTTPServer(('127.0.0.1', 0), Victim)
    destination = 'http://127.0.0.1:%s/next' % victim.server_port
    class Api(Victim):
        def do_GET(self):
            self.send_response(302 if mode == 'redirect' else 200)
            self.send_header('Location' if mode == 'redirect' else 'Link',
                             destination if mode == 'redirect' else '<%s>; rel="next"' % destination)
            self.end_headers()
            self.wfile.write(b'[]')
    api = ThreadingHTTPServer(('127.0.0.1', 0), Api)
    threads = [threading.Thread(target=s.serve_forever) for s in (api, victim)]
    for thread in threads:
        thread.start()
    try:
        with patch.object(overlap, 'API', 'http://127.0.0.1:%s' % api.server_port):
            checker = overlap.Checker(token='fixture-token', cache_dir=tmp_path)
            try:
                checker._pages(overlap.API + '/start', 3)
            except overlap.Unknown:
                pass
        assert received == [], 'foreign origin received the bearer token'
    finally:
        for server in (api, victim):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

"""CLI publication integration: real remotes, offline model stubs only."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import coach_apply as ca
import coach_eval as ce
import coach_model_bench as cb
from coach_publish import publish_run


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'no-global'))
    for key, value in [('GIT_AUTHOR_NAME', 'Test'), ('GIT_COMMITTER_NAME', 'Test'),
                       ('GIT_AUTHOR_EMAIL', 'test@example.com'), ('GIT_COMMITTER_EMAIL', 'test@example.com')]:
        monkeypatch.setenv(key, value)
    upstream, local, peer = [tmp_path / x for x in ('upstream', 'local', 'peer')]
    upstream.mkdir()
    git(upstream, 'init', '--bare', '--initial-branch=main')
    git(tmp_path, 'clone', str(upstream), str(local))
    ledger = local / 'research/state/spend.jsonl'
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"seed":true}\n')
    git(local, 'add', '.')
    git(local, 'commit', '-m', 'seed')
    git(local, 'push', '-u', 'origin', 'main')
    git(tmp_path, 'clone', str(upstream), str(peer))
    monkeypatch.setenv('COACH_DATA', str(local))
    # Failure reporting must never create real cards or wait in these tests.
    monkeypatch.setenv('COACH_HERMES', '/nonexistent/hermes')
    monkeypatch.setattr(ca.time, 'sleep', lambda _: None)
    return upstream, local, peer


def invoke(local, kind, *, flags=(), concurrent=None, outcome=0, out=True):
    report = local / 'eval/reports/test.md'
    candidate = local.parent / 'candidate.json'
    candidate.write_text('{}')

    def run(*args, **kwargs):
        with (local / 'research/state/spend.jsonl').open('a') as f:
            f.write('{"run":true}\n')
        if kind == 'bench' and kwargs.get('replies_path'):
            assert report.parent.is_dir(), 'CLI must create the cache directory before replay'
            kwargs['replies_path'].write_text('{"reply":"offline"}\n')
        if concurrent:
            concurrent()
        return {'verdict': {0: 'PASS', 1: 'REJECT', 2: 'INCOMPLETE'}[outcome], 'complete': outcome == 0}

    if kind == 'eval':
        mod, run_name, md_name = ce, 'run_behavior', 'behavior_markdown'
        argv = ['behavior', '--candidate', str(candidate)]
    else:
        mod, run_name, md_name = cb, 'run', 'markdown'
        argv = ['run', '--bench', 'unused.jsonl', '--model', 'offline']
    if out:
        argv += ['--out', str(report)]
    with patch.object(mod, run_name, side_effect=run), patch.object(mod, md_name, return_value='offline report'):
        return mod.main(argv + list(flags))


@pytest.mark.parametrize('kind', ['eval', 'bench'])
def test_publishes_rows_and_all_reports(repos, kind):
    upstream, local, _ = repos
    (local / 'unrelated.txt').write_text('keep local')
    git(local, 'add', 'unrelated.txt')
    assert invoke(local, kind) == 0
    assert json.loads(git(upstream, 'show', 'main:eval/reports/test.json'))['complete']
    assert git(upstream, 'show', 'main:eval/reports/test.md') == 'offline report'
    assert '"run":true' in git(upstream, 'show', 'main:research/state/spend.jsonl')
    if kind == 'bench':
        assert 'offline' in git(upstream, 'show', 'main:eval/reports/test.replies.jsonl')
    assert git(local, 'status', '--porcelain') == 'A  unrelated.txt'


@pytest.mark.parametrize('kind', ['eval', 'bench'])
def test_concurrent_append_unions(repos, kind):
    upstream, local, peer = repos
    def concurrent():
        with (peer / 'research/state/spend.jsonl').open('a') as f:
            f.write('{"peer":true}\n')
        git(peer, 'commit', '-am', 'peer append')
        git(peer, 'push')
    assert invoke(local, kind, concurrent=concurrent) == 0
    rows = git(upstream, 'show', 'main:research/state/spend.jsonl').splitlines()
    assert {json.loads(row).popitem()[0] for row in rows} == {'seed', 'peer', 'run'}
    assert git(local, 'status', '--porcelain') == ''


@pytest.mark.parametrize('kind', ['eval', 'bench'])
def test_no_publish_leaves_local(repos, kind):
    upstream, local, _ = repos
    before = git(upstream, 'rev-parse', 'main')
    assert invoke(local, kind, flags=['--no-publish']) == 0
    assert git(upstream, 'rev-parse', 'main') == before
    assert git(local, 'rev-parse', 'HEAD') == before
    assert git(local, 'status', '--porcelain')


@pytest.mark.parametrize('kind,outcome', [('eval', 0), ('eval', 1), ('eval', 2), ('bench', 0), ('bench', 2)])
def test_push_failure_keeps_exit_code(repos, kind, outcome, capsys):
    upstream, local, _ = repos
    hook = upstream / 'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nprintf "publication refused by test\\n" >&2\nexit 1\n')
    hook.chmod(0o755)
    assert invoke(local, kind, outcome=outcome) == outcome
    assert 'publication refused by test' in capsys.readouterr().err
    assert git(local, 'status', '--porcelain') == ''
    assert git(local, 'rev-parse', 'HEAD') != git(upstream, 'rev-parse', 'main')


@pytest.mark.parametrize('kind', ['eval', 'bench'])
def test_ledger_without_report(repos, kind):
    upstream, local, _ = repos
    assert invoke(local, kind, out=False) == 0
    assert '"run":true' in git(upstream, 'show', 'main:research/state/spend.jsonl')
    assert git(local, 'status', '--porcelain') == ''


def test_outside_symlink_and_no_change_not_published(repos):
    upstream, local, _ = repos
    before = git(upstream, 'rev-parse', 'main')
    outside = local.parent / 'outside.md'
    link = local / 'linked.md'
    link.symlink_to(outside)
    with publish_run(local, link):
        outside.write_text('private external content')
    assert git(upstream, 'rev-parse', 'main') == before
    assert git(local, 'status', '--porcelain') == '?? linked.md'


def test_report_only_command(repos):
    upstream, local, _ = repos
    out = local / 'eval/reports/detector.md'
    with patch.object(ce, 'run_detector', return_value={'verdict': 'REJECT'}), \
            patch.object(ce, 'detector_markdown', return_value='detector report'):
        assert ce.main(['detector', '--candidate', 'unused', '--out', str(out)]) == 1
    assert git(upstream, 'show', 'main:eval/reports/detector.md') == 'detector report'
    assert git(local, 'status', '--porcelain') == ''


def test_exception_preserves_accounting_and_exception(repos):
    upstream, local, _ = repos
    with pytest.raises(RuntimeError, match='evaluation failed'):
        with publish_run(local):
            with (local / 'research/state/spend.jsonl').open('a') as f:
                f.write('{"reserve":true}\n')
            raise RuntimeError('evaluation failed')
    assert '"reserve":true' in git(upstream, 'show', 'main:research/state/spend.jsonl')


def test_non_git_data(tmp_path):
    with publish_run(tmp_path, tmp_path / 'report.md'):
        (tmp_path / 'report.md').write_text('offline')
    assert not (tmp_path / '.git').exists()

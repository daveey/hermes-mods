"""Bootstrapped discovery + actual completion tool, only disposable state."""
import sys
import os
import json
import tempfile
import subprocess
import time
from pathlib import Path

core = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(core))
import hermes_bootstrap
import hermes_cli
hermes_cli.__path__ = [str(core / 'hermes_cli')]

with tempfile.TemporaryDirectory(prefix='owner-runtime-') as tmp:
    root = Path(tmp)
    home = root / 'home'
    home.mkdir()
    for key in tuple(os.environ):
        if key.startswith('HERMES_KANBAN_'):
            os.environ.pop(key)
    # Test harness only: all Kanban paths are asserted below this temporary home.
    # Like scripts/run_tests.sh, remove inherited worker/delegation provenance.
    os.environ.pop('HERMES_DELEGATED_CHILD_CONTEXT', None)
    os.environ.pop('HERMES_DELEGATED_CHILD_OWNER', None)
    os.environ.update(HERMES_HOME=str(home), HERMES_KANBAN_HOME=str(home), HERMES_PROFILE='default', HERMES_SESSION_ID='isolated-owner-fixture')
    # Explicit test-only opt-in; no live configuration changes.
    (home / 'worktree-owner.json').write_text(json.dumps({'linux_sudo_readlink': True}))
    (home / 'config.yaml').write_text('plugins:\n  enabled:\n    - worktree-owner\n')
    (home / 'plugins').mkdir()
    (home / 'plugins/worktree-owner').symlink_to(Path(__file__).resolve().parents[1])
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    discover_plugins()
    pm = get_plugin_manager()
    plugin = pm._plugins['worktree-owner']
    assert plugin.enabled and plugin.module and not plugin.error, str(plugin)
    m = plugin.module
    hooks = [h for h in pm._hooks['post_tool_call'] if h.__module__ == m.__name__]
    assert len(hooks) == 1
    hook = hooks[0]
    m.LOG = root / 'owner.log'
    m.ARCHIVE = root / 'archives'
    print('DISCOVERY', json.dumps({'module': m.__file__, 'hooks': plugin.hooks_registered}))
    paths = m.live_cwds()
    assert os.getcwd() in paths
    print('LIVE_LINUX_SCAN', json.dumps({'cwd_count': len(paths), 'own_cwd_present': True}))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_workspace as kbw
    from hermes_cli import kanban_db_connect as kbc
    from tools import kanban_tools as kt
    kb.init_db()
    env = dict(os.environ, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.com', GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.com')
    def git(cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], env=env, check=True, text=True, capture_output=True).stdout.strip()
    repo = root / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    (repo / 'tracked').write_text('base')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    git(repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    m.REPO_GLOBS = [str(repo / '.git')]
    with kbc.connect() as conn:
        db = conn.execute('PRAGMA database_list').fetchone()[2]
        assert Path(db).is_relative_to(home)
        for state in ('clean', 'dirty', 'untracked', 'unpushed'):
            os.environ.pop('HERMES_KANBAN_TASK', None)
            tid = kb.create_task(conn, title='isolated fixture ' + state, assignee='default')
            ws = kbw.resolve_workspace(kb.get_task(conn, tid))
            kbw.set_workspace_path(conn, tid, ws)
            kb.claim_task(conn, tid)
            run = kb.get_task(conn, tid).current_run_id
            os.environ.update(HERMES_KANBAN_TASK=tid, HERMES_KANBAN_RUN_ID=str(run), HERMES_KANBAN_DB=db, HERMES_KANBAN_WORKSPACE=str(ws))
            tree = ws / 'tree'
            start = time.time()
            git(repo, 'worktree', 'add', '-qb', state, str(tree))
            hook(tool_name='terminal', args={'command': f'git worktree add {tree}', 'workdir': str(repo)}, duration_ms=(time.time()-start)*1000)
            gd = Path(git(tree, 'rev-parse', '--absolute-git-dir'))
            marker = json.loads((gd / m.MARKER).read_text())
            assert marker['run_id'] == str(run) and marker['board_db'] == db
            if state == 'dirty':
                (tree / 'tracked').write_text('dirty')
            if state == 'untracked':
                (tree / 'new').write_text('untracked')
            if state == 'unpushed':
                (tree / 'tracked').write_text('unpushed')
                git(tree, 'commit', '-qam', 'unpushed')
            reject_args = {'summary': 'fixture', 'created_cards': ['t_nonexistent']}
            rejected = kt._handle_complete(reject_args)
            assert json.loads(rejected).get('error'), rejected
            hook(tool_name='kanban_complete', args=reject_args, result=rejected)
            assert tree.exists() and kb.get_task(conn, tid).status == 'running'
            args = {'summary': 'isolated fixture verified'}
            result = kt._handle_complete(args)
            assert json.loads(result).get('ok') is True, result
            assert tree.exists(), 'core deleted tree before post-tool hook'
            hook(tool_name='kanban_complete', args=args, result=result)
            assert not tree.exists(), m.LOG.read_text()
            assert str(tree) not in git(repo, 'worktree', 'list')
            print('ACTUAL_TOOL_AND_REGISTERED_HOOK', json.dumps({'state': state, 'rejection_kept': True, 'core_preserved': True, 'hook_removed': True, 'run_type': type(json.loads(result)['run_id']).__name__}))
    print('PASS: no real boards or worktrees modified')

"""Real bare-origin publication fixtures; only the external CI service is injected."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location('skill_publish_test', HERE / 'skill_publish.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

# PATH of the MacBook Desktop serve backend (launchd child): no /opt/homebrew/bin, so no gitleaks.
DESKTOP_SERVE_PATH = os.pathsep.join([
    '/Users/you/.local/bin', '/Users/you/.cargo/bin', '/Users/you/.nix-profile/bin',
    '/nix/var/nix/profiles/default/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'])
# GitHub Actions sets CI=true. There a missing tool is a provisioning bug, not a host quirk: a skip would
# report green while the real pre-commit tests never ran (as with PR #61/#62), so fail instead.
UNDER_CI = os.environ.get('CI', '').lower() == 'true'

# The worktree part of core's `hermes kanban source-custody remove` contract, for these fixtures only: the owner
# id must match the created marker in the tree's own gitdir (exit 1 otherwise), removal is never forced, and a
# tree whose HEAD is on no remote ref or that Git will not remove is kept (exit 3).
CUSTODY_LAUNCHER = '''#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
if args[:5] != ['kanban', 'source-custody', 'remove', '--kind', 'worktree'] or args[5] != '--path' \\
        or args[7] != '--expected-owner' or len(args) != 9:
    sys.exit(2)
path, owner = args[6], args[8]
def git(*a):
    return subprocess.run(['git', '-C', path, *a], capture_output=True, text=True)
gitdir = git('rev-parse', '--absolute-git-dir').stdout.strip()
try:
    rec = json.load(open(os.path.join(gitdir, 'hermes-owner.json')))
except (OSError, ValueError):
    print('source-custody: owner marker unreadable', file=sys.stderr); sys.exit(1)
if owner != 'created:%s/%s' % (rec.get('tool'), rec.get('creation_id')) or rec.get('path') != os.path.realpath(path):
    print('source-custody: owner marker is a different owner', file=sys.stderr); sys.exit(1)
if not git('branch', '-r', '--contains', 'HEAD').stdout.strip():
    print('source-custody: custody refused: unpublished commit', file=sys.stderr); sys.exit(3)
common = git('rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()
done = subprocess.run(['git', '-C', os.path.dirname(common), 'worktree', 'remove', path], capture_output=True, text=True)
if done.returncode:
    print('source-custody: git refused to remove the worktree (dirty, locked or changed)', file=sys.stderr); sys.exit(3)
'''


def skip_or_fail_in_ci(test, reason, fix):
    """skipTest(reason) on a dev host; under CI=true fail, naming the provisioning fix."""
    if UNDER_CI:
        test.fail(f'{reason}, under CI=true: {fix}')
    test.skipTest(reason)


class SkipOrFailInCiTests(unittest.TestCase):
    def outcome(self, under_ci):
        class Probe(unittest.TestCase):
            def runTest(self):
                skip_or_fail_in_ci(self, 'real pre-commit tests require gitleaks', 'provision it')
        result = unittest.TestResult()
        with patch(__name__ + '.UNDER_CI', under_ci):
            Probe().run(result)
        return result

    def test_dev_host_skips_with_the_reason(self):
        result = self.outcome(False)
        self.assertEqual([r for _, r in result.skipped], ['real pre-commit tests require gitleaks'])
        self.assertEqual(result.failures, [])

    def test_ci_fails_naming_the_fix(self):
        result = self.outcome(True)
        self.assertEqual(result.skipped, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn('real pre-commit tests require gitleaks, under CI=true: provision it',
                      result.failures[0][1])


class BudgetTests(unittest.TestCase):
    def test_shared_deadline_caps_git_ci_and_reconcile_subprocesses(self):
        now = [100.0]
        with publisher.operation_budget(45, lambda: now[0]):
            now[0] = 140.0
            with patch.object(publisher.subprocess, 'Popen') as popen:
                process = popen.return_value.__enter__.return_value
                process.communicate.return_value = (b'', b'')
                process.returncode = 0
                for command, cap in [(['git', 'fetch'], 30), (['gh', 'run'], 20),
                                     (['python', 'reconcile.py'], 90)]:
                    publisher.bounded(command, timeout=cap)
                    self.assertEqual(process.communicate.call_args.kwargs['timeout'], 5)
                now[0] = 145.0
                with self.assertRaisesRegex(ValueError, 'publication deadline'):
                    publisher.bounded(['git', 'push'], timeout=30)
                self.assertEqual(popen.call_count, 3)
        self.assertEqual(publisher.remaining(30), 30)

    def test_registered_hook_allows_work_cleanup_and_two_reaps_under_minute(self):
        spec = importlib.util.spec_from_file_location('budget_guard_test', HERE / '__init__.py')
        assert spec is not None and spec.loader is not None
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        hooks = {}
        class Context:
            def register_hook(self, name, callback):
                hooks[name] = callback
        plugin.register(Context())
        allowance = getattr(hooks['post_tool_call'], 'hermes_hook_timeout', 30)
        self.assertGreater(allowance, publisher.WORK_BUDGET + publisher.CLEANUP_BUDGET
                           + 2 * publisher.REAP_BUDGET)
        self.assertLessEqual(allowance, 60)


class CredentialTests(unittest.TestCase):
    def test_https_fetch_and_push_use_subprocess_gh_helper(self):
        for args in [('fetch', 'origin', 'main'),
                     ('-c', 'core.hooksPath=.githooks', 'push', 'origin', 'HEAD:main')]:
            with self.subTest(args=args):
                calls = []
                def capture(command, **kwargs):
                    calls.append((command, kwargs))
                    output = b'https://github.com/example/mods.git\n' if 'get-url' in command else b''
                    return subprocess.CompletedProcess(command, 0, output, b'')
                with patch.object(publisher, 'bounded', side_effect=capture):
                    publisher.run('/example', *args, env={'GIT_CONFIG_COUNT': '1'})
                command, kwargs = calls[-1]
                self.assertIn('credential.https://github.com.helper=', command)
                self.assertIn('credential.https://github.com.helper=!gh auth git-credential', command)
                self.assertNotIn('GIT_CONFIG_COUNT', kwargs['env'])
                self.assertEqual(command[-len(args):], list(args))
                self.assertEqual('--push' in calls[0][0], 'push' in args)

    def test_non_https_origins_have_no_credential_override(self):
        for url in ['git@github.com:example/mods.git', 'ssh://git@example.com/mods',
                    'file:///example/mods.git', '/example/mods.git']:
            calls = []
            def capture(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, url.encode() + b'\n', b'')
            with patch.object(publisher, 'bounded', side_effect=capture):
                publisher.run('/example', 'fetch', 'origin', 'main')
            self.assertFalse(any('credential.' in arg for arg in calls[-1]))


class ToolPathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.a = self.base / 'a'
        self.b = self.base / 'b'
        self.a.mkdir()
        self.b.mkdir()
        self.absent = self.base / 'absent'

    def test_appends_existing_missing_dirs_after_inherited_order(self):
        inherited = os.pathsep.join(['/x', '/y', str(self.a), '/x'])
        tools = (str(self.a), str(self.absent), str(self.b), str(self.b) + '/')
        with patch.object(publisher, 'TOOL_DIRS', tools, create=True):
            self.assertEqual(publisher.tool_path(inherited).split(os.pathsep),
                             ['/x', '/y', str(self.a), '/x', str(self.b)])
            # Idempotent: run() re-cleans an env clean_env already built.
            once = publisher.clean_env({'PATH': inherited})['PATH']
            self.assertEqual(publisher.clean_env({'PATH': once})['PATH'], once)

    def test_clean_env_and_bounded_subprocesses_get_tool_path(self):
        with patch.object(publisher, 'TOOL_DIRS', (str(self.a),), create=True):
            env = publisher.clean_env({'PATH': '/usr/bin', 'GIT_DIR': '/decoy'})
            self.assertEqual(env, {'PATH': '/usr/bin' + os.pathsep + str(self.a)})
            self.assertEqual(publisher.clean_env({})['PATH'],
                             os.defpath + os.pathsep + str(self.a))
            with patch.dict(os.environ, {'PATH': DESKTOP_SERVE_PATH}), \
                    patch.object(publisher.subprocess, 'Popen') as popen:
                process = popen.return_value.__enter__.return_value
                process.communicate.return_value = (b'', b'')
                process.returncode = 0
                publisher.bounded(['gh', 'run', 'list'], timeout=5)
                publisher.run('/example', 'status')
        self.assertEqual(popen.call_count, 2)
        for call in popen.call_args_list:
            self.assertEqual(call.kwargs['env']['PATH'],
                             DESKTOP_SERVE_PATH + os.pathsep + str(self.a))

    def test_default_dirs_are_homebrew_usr_local_and_home_local(self):
        self.assertEqual(publisher.TOOL_DIRS,
                         ('/opt/homebrew/bin', '/usr/local/bin', '~/.local/bin'))
        (self.base / '.local/bin').mkdir(parents=True)
        with patch.dict(os.environ, {'HOME': str(self.base)}):
            added = publisher.tool_path('/nowhere').split(os.pathsep)[1:]
        self.assertEqual(added, [d for d in ['/opt/homebrew/bin', '/usr/local/bin',
                                             str(self.base / '.local/bin')] if os.path.isdir(d)])


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self._setup_fixture()

    def assert_kept_worktree(self, reason):
        """A failed publication's worktree holds unpublished work: core refuses, the tree stays, and the keep is
        written beside it and in the retry log."""
        self.assertEqual(len(self.reaped), 1)
        worktree, owner_id, kept = self.reaped[0]
        self.assertRegex(owner_id, r'^created:skill-publish/[0-9a-f]{32}$')
        self.assertIn(reason, kept)
        self.assertTrue(Path(worktree).is_dir())
        note = (Path(worktree).parent / 'worktree-kept.txt').read_text()
        self.assertIn(worktree, note)
        self.assertIn(owner_id, note)
        self.assertIn(reason, note)
        log = (Path(os.environ['HERMES_HOME']) / 'logs/skill-publish-retry.log').read_text()
        self.assertIn('kept publication worktree ' + worktree, log)

    def _setup_fixture(self):
        if not shutil.which('gitleaks'):
            skip_or_fail_in_ci(self, 'real pre-commit tests require gitleaks',
                               'put gitleaks v8.28.0 on PATH (plugin-suites.yml "Suite provisions")')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'live'
        self.origin = self.base / 'origin.git'
        self.git(self.base, 'init', '--bare', '-q', str(self.origin))
        self.git(self.base, 'init', '-q', '-b', 'main', str(self.root))
        self.git(self.root, 'config', 'user.name', 'Fixture Author')
        self.git(self.root, 'config', 'user.email', 'author@example.com')
        self.git(self.root, 'remote', 'add', 'origin', str(self.origin))
        shutil.copytree(REPO / '.githooks', self.root / '.githooks')
        shutil.copy(REPO / '.gitleaks.toml', self.root)
        self.path = 'skills/testing/sample/SKILL.md'
        self.target = self.root / self.path
        self.target.parent.mkdir(parents=True)
        self.target.write_text('base\n')
        # An actual pre-push race, not a stub of git/publish subprocesses.
        race_hook = self.root / '.githooks/pre-push'
        race_hook.write_text('''#!/bin/sh
count=0
if [ -e "$PUBLISH_RACE_SENTINEL" ]; then read count < "$PUBLISH_RACE_SENTINEL"; fi
if [ -n "$PUBLISH_RACE_REPO" ] && [ "$count" -lt "${PUBLISH_RACE_REPEATS:-1}" ]; then
  count=$((count + 1))
  printf '%s\\n' "$count" > "$PUBLISH_RACE_SENTINEL"
  racer="$PUBLISH_RACE_REPO"
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR PUBLISH_RACE_REPO
  git -C "$racer" -c core.hooksPath=.githooks commit --allow-empty -qm race || exit 1
  git -C "$racer" -c core.hooksPath=.githooks push -q origin HEAD:main || exit 1
fi
''')
        race_hook.chmod(0o755)
        self.denylist = self.base / 'denylist.txt'
        self.denylist.write_text('PRIVATE_FIXTURE_MARKER\n')
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.base / 'hermes-home'),
                                          'HERMES_MODS_DENYLIST': str(self.denylist),
                                          'LIVE_CHECKOUT_ROOT': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Core's `source-custody remove` is not in this repo: a launcher with its worktree contract stands in at
        # this fixture home's install path (so the detached retry worker finds it too). In-process publications
        # reap inline so a test can read the result.
        self.cli = self.base / 'hermes-home/hermes-agent/.hermes/bin/hermes'
        self.cli.parent.mkdir(parents=True)
        self.cli.write_text(CUSTODY_LAUNCHER)
        self.cli.chmod(0o755)
        self.reaped = []
        def reap_now(worktree, owner_id):
            self.reaped.append((str(worktree), owner_id, publisher.reap(worktree, owner_id)))
        seam = patch.object(publisher, 'spawn_reaper', side_effect=reap_now)
        seam.start()
        self.addCleanup(seam.stop)
        self.git(self.root, 'add', '.')
        self.git(self.root, '-c', 'core.hooksPath=.githooks', 'commit', '-qm', 'base')
        self.git(self.root, '-c', 'core.hooksPath=.githooks', 'push', '-q', 'origin', 'HEAD:main')
        self.git(self.root, 'fetch', '-q', 'origin', 'main')
        self.head = self.git(self.root, 'rev-parse', 'HEAD')
        self.index = (self.root / '.git/index').read_bytes()
        self.target.write_text('published\n')

    def git(self, root, *args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def publish(self, paths=None, **kwargs):
        return publisher.publish(self.root, paths or [self.path], session_id='session-example',
                                 task_id='card-example', ci=kwargs.pop('ci', lambda *_: 'green'), **kwargs)

    def assert_no_worktrees(self):
        # A publication made by the detached retry worker is reaped by its own detached child: allow it to land.
        gone = lambda: self.git(self.root, 'worktree', 'list', '--porcelain').count('worktree ') == 1
        deadline = time.monotonic() + 20
        while not gone() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(self.git(self.root, 'worktree', 'list', '--porcelain').count('worktree '), 1)
        self.assertFalse(self.git(self.root, 'worktree', 'list').count('skill-publish-') > 0)

    def test_total_deadline_stops_after_commit_and_keeps_the_unpublished_worktree(self):
        now = [100.0]
        original = publisher.bounded
        def advance_after_commit(command, **kwargs):
            result = original(command, **kwargs)
            if 'commit' in command:
                now[0] += 45.0
            return result
        with patch.object(publisher, 'bounded', side_effect=advance_after_commit):
            with self.assertRaisesRegex(ValueError, 'publication deadline.*archive:'):
                self.publish(clock=lambda: now[0])
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
        self.assertTrue(list((self.base / 'hermes-home/kanban/evidence/mods-reconcile').glob(
            'skill-publish-*/publish.patch')))
        self.assert_kept_worktree('custody refused: unpublished commit')
        # An expired context must not poison the next publication on this worker.
        self.assertEqual(self.publish()['status'], 'published')

    def test_publish_clean_and_later_ff_inherited_identity(self):
        result = self.publish()
        self.assertEqual(result['status'], 'published')
        self.assertEqual((self.root / '.git/index').read_bytes(), self.index)
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), self.head)
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')
        self.assertEqual(self.git(self.origin, 'show', '-s', '--format=%an <%ae>', 'main'),
                         'Fixture Author <author@example.com>')
        subject = self.git(self.origin, 'show', '-s', '--format=%s', 'main')
        self.assertIn('session-example', subject)
        self.assertIn('card-example', subject)
        self.assertIn('sample', subject)
        self.assertTrue((Path(result['archive']) / 'manifest.json').is_file())
        self.git(self.root, 'merge', '--ff-only', 'origin/main')
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assert_no_worktrees()

    def test_unchanged_selection_does_not_block_changed_path(self):
        unchanged = 'skills/testing/sample/references/unchanged.md'
        (self.root / unchanged).parent.mkdir()
        (self.root / unchanged).write_text('unchanged\n')
        self.git(self.root, 'add', unchanged)
        self.git(self.root, '-c', 'core.hooksPath=.githooks', 'commit', '-qm', 'fixture doc')
        self.git(self.root, '-c', 'core.hooksPath=.githooks', 'push', '-q', 'origin', 'HEAD:main')
        result = self.publish([self.path, unchanged])
        self.assertEqual(result['status'], 'published')
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assertEqual((self.root / unchanged).read_text(), 'unchanged\n')
        self.assert_no_worktrees()

    def test_multiple_paths_new_support_and_unrelated_dirt(self):
        extra = 'skills/testing/sample/references/topic.md'
        (self.root / extra).parent.mkdir()
        (self.root / extra).write_text('new reference\n')
        (self.root / 'unrelated.txt').write_text('leave alone\n')
        result = self.publish([self.path, extra, self.path])
        self.assertEqual(result['status'], 'published')
        self.assertFalse((self.root / extra).exists())
        self.assertEqual((self.root / 'unrelated.txt').read_text(), 'leave alone\n')
        self.assertEqual(self.git(self.origin, 'show', 'main:' + extra), 'new reference')
        self.assert_no_worktrees()

    def test_scope_rejections_preserve_edits(self):
        for name in ['plugins/example/README.md', '../escape.md',
                     'skills/testing/sample/scripts/guide.md',
                     'skills/testing/sample/references/tool.py',
                     'skills/testing/sample/assets/tool.sh',
                     'skills/testing/sample/templates/tool.js',
                     'skills/testing/sample/references/../SKILL.md']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.publish([name])
        outside = self.base / 'outside.md'
        outside.write_text('outside')
        alias = self.target.parent / 'references'
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.publish(['skills/testing/sample/references/outside.md'])
        self.assertEqual(outside.read_text(), 'outside')
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)

    def test_desktop_serve_path_reaches_gitleaks_through_tool_dirs(self):
        real = shutil.which('gitleaks')
        assert real is not None
        if shutil.which('gitleaks', path=DESKTOP_SERVE_PATH):
            skip_or_fail_in_ci(self, 'gitleaks is on the Desktop serve PATH on this host',
                               'install it outside the Desktop serve PATH (not /usr/bin, /bin, /usr/sbin, /sbin)')
        tools = self.base / 'tools'
        tools.mkdir()
        (tools / 'gitleaks').symlink_to(real)
        with patch.dict(os.environ, {'PATH': DESKTOP_SERVE_PATH}), \
                patch.object(publisher, 'TOOL_DIRS', (str(tools),), create=True):
            result = self.publish()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), result['commit'])
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assert_no_worktrees()

    def test_truly_missing_gitleaks_still_refused_and_preserved(self):
        if shutil.which('gitleaks', path=DESKTOP_SERVE_PATH):
            skip_or_fail_in_ci(self, 'gitleaks is on the Desktop serve PATH on this host',
                               'install it outside the Desktop serve PATH (not /usr/bin, /bin, /usr/sbin, /sbin)')
        empty = self.base / 'empty-tools'
        empty.mkdir()
        with patch.dict(os.environ, {'PATH': DESKTOP_SERVE_PATH}), \
                patch.object(publisher, 'TOOL_DIRS', (str(empty), str(self.base / 'absent')),
                             create=True):
            with self.assertRaisesRegex(ValueError, r'privacy check\(s\) cannot run: gitleaks'):
                self.publish()
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
        self.assertEqual(len(list((self.base / 'hermes-home/kanban/evidence/mods-reconcile').glob(
            'skill-publish-*/publish.patch'))), 1)
        self.assert_kept_worktree('git refused to remove the worktree')

    def test_red_and_unknown_ci_one_skip_no_mutation(self):
        for state in ['red', 'unknown']:
            result = self.publish(ci=lambda *_: state)
            self.assertEqual(result, {'status': 'skipped', 'reason': 'latest main CI ' + state})
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assert_no_worktrees()

    def test_real_precommit_refusal_preserves_and_archives(self):
        self.target.write_text('PRIVATE_FIXTURE_MARKER\n')
        with self.assertRaisesRegex(ValueError, 'denylist'):
            self.publish()
        self.assertEqual(self.target.read_text(), 'PRIVATE_FIXTURE_MARKER\n')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
        self.assertEqual(len(list((self.base / 'hermes-home/kanban/evidence/mods-reconcile').glob('skill-publish-*/publish.patch'))), 1)
        self.assert_kept_worktree('git refused to remove the worktree')

    def test_real_push_race_fetch_rebase_retry(self):
        racer = self.base / 'racer'
        self.git(self.base, 'clone', '-q', '-b', 'main', str(self.origin), str(racer))
        self.git(racer, 'config', 'user.name', 'Fixture Racer')
        self.git(racer, 'config', 'user.email', 'racer@example.com')
        calls = []
        def ci(root, sha):
            calls.append(sha)
            return 'green'
        with patch.dict(os.environ, {'PUBLISH_RACE_REPO': str(racer),
                                    'PUBLISH_RACE_SENTINEL': str(self.base / 'raced')}):
            result = self.publish(ci=ci)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')
        self.assertEqual(self.git(self.origin, 'show', '-s', '--format=%s', 'main^'), 'race')
        self.assert_no_worktrees()

    def test_retry_exhaustion_preserves_source_and_archive(self):
        racer = self.base / 'racer'
        self.git(self.base, 'clone', '-q', '-b', 'main', str(self.origin), str(racer))
        self.git(racer, 'config', 'user.name', 'Fixture Racer')
        self.git(racer, 'config', 'user.email', 'racer@example.com')
        calls = []
        def ci(root, sha):
            calls.append(sha)
            return 'green'
        sentinel = self.base / 'raced'
        with patch.dict(os.environ, {'PUBLISH_RACE_REPO': str(racer),
                                    'PUBLISH_RACE_SENTINEL': str(sentinel),
                                    'PUBLISH_RACE_REPEATS': '9'}):
            with self.assertRaises(ValueError):
                self.publish(ci=ci)
        self.assertEqual(sentinel.read_text().strip(), '4')
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'base')
        self.assertEqual(len(list((self.base / 'hermes-home/kanban/evidence/mods-reconcile').glob('skill-publish-*/publish.patch'))), 1)
        self.assert_kept_worktree('custody refused: unpublished commit')

    def test_new_writer_during_ci_is_never_overwritten(self):
        def ci(*_):
            self.target.write_text('new concurrent writer\n')
            return 'green'
        with self.assertRaisesRegex(ValueError, 'new writer'):
            self.publish(ci=ci)
        self.assertEqual(self.target.read_text(), 'new concurrent writer\n')
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')
        self.assert_no_worktrees()

    def test_selected_staged_changes_refused(self):
        self.git(self.root, 'add', self.path)
        with self.assertRaisesRegex(ValueError, 'staged'):
            self.publish()
        self.assertEqual(self.target.read_text(), 'published\n')

    def test_real_hook_registration_and_write_file_result(self):
        # Substitute only the external gh CI executable; all Git and hooks stay real.
        bin_dir = self.base / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text('''#!/bin/sh
sha=$(git rev-parse origin/main)
printf '[{"headSha":"%s","status":"completed","conclusion":"%s","workflowName":"CI","createdAt":"2026-01-01"}]' "$sha" "${PUBLISH_CI_CONCLUSION:-success}"
''')
        gh.chmod(0o755)
        spec = importlib.util.spec_from_file_location('live_guard_publish_test', HERE / '__init__.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        hooks = {}
        class Context:
            def register_hook(self, name, fn):
                hooks[name] = fn
        plugin.register(Context())
        self.assertIn('post_tool_call', hooks)
        with patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}):
            for conclusion, expected in [('failure', 'red'), ('skipped', 'unknown')]:
                with patch.dict(os.environ, {'PUBLISH_CI_CONCLUSION': conclusion}):
                    with self.assertLogs(plugin.logger, level='INFO') as skipped:
                        hooks['post_tool_call'](tool_name='skill_manage', args={'action': 'write_file'},
                            result={'success': True, 'path': str(self.target)}, status='ok')
                self.assertEqual(len(skipped.output), 1)
                self.assertIn('latest main CI ' + expected, skipped.output[0])
                self.assertEqual(self.target.read_text(), 'published\n')
                self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
            with self.assertLogs(plugin.logger, level='INFO') as logs:
                hooks['post_tool_call'](tool_name='skill_manage', args={'action': 'write_file',
                    'name': 'sample', 'file_path': 'SKILL.md'},
                    result=json.dumps({'success': True, 'path': str(self.target)}),
                    task_id='card-example', session_id='session-example', status='ok')
        self.assertEqual(len(logs.output), 1)
        self.assertIn("'status': 'published'", logs.output[0])
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assert_no_worktrees()

    def fake_gh(self):
        """A gh executable whose push-CI answer for the fetched tip is read from a state file."""
        bin_dir = self.base / 'bin'
        bin_dir.mkdir(exist_ok=True)
        state = self.base / 'ci-state'
        gh = bin_dir / 'gh'
        gh.write_text('''#!/bin/sh
sha=$(git rev-parse origin/main)
read status conclusion < "$PUBLISH_CI_STATE"
printf '[{"headSha":"%s","status":"%s","conclusion":"%s","workflowName":"CI","createdAt":"2026-01-01"}]' "$sha" "$status" "$conclusion"
''')
        gh.chmod(0o755)
        return bin_dir, state

    def load_plugin(self, name):
        spec = importlib.util.spec_from_file_location(name, HERE / '__init__.py')
        assert spec is not None and spec.loader is not None
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        hooks = {}
        class Context:
            def register_hook(self, hook, fn):
                hooks[hook] = fn
        plugin.register(Context())
        return plugin, hooks

    def wait_for(self, predicate, timeout=30.0):
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.25)
        return predicate()

    def test_in_progress_ci_then_green_publishes_without_new_skill_manage_call(self):
        """Regression: a skip only because main CI is still running must not strand the edit."""
        bin_dir, state = self.fake_gh()
        state.write_text('in_progress null\n')
        plugin, hooks = self.load_plugin('live_guard_pending_test')
        pending = self.base / 'hermes-home/cache/skill-publish-pending'
        with patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                                    'PUBLISH_CI_STATE': str(state),
                                    'HERMES_SKILL_PUBLISH_RETRY_INTERVAL': '0.5'}):
            with self.assertLogs(plugin.logger, level='INFO') as logs:
                hooks['post_tool_call'](tool_name='skill_manage', args={'action': 'write_file'},
                    result={'success': True, 'path': str(self.target)},
                    task_id='card-example', session_id='session-desktop', status='ok')
            self.assertIn("'status': 'skipped'", logs.output[0])
            # Still running: nothing is published while the tip's CI is in progress.
            self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
            self.assertEqual(self.target.read_text(), 'published\n')
            # CI goes green later; no further skill_manage call is made.
            state.write_text('completed success\n')
            published = lambda: self.git(self.origin, 'show', 'main:' + self.path) == 'published'
            self.assertTrue(self.wait_for(published), 'in-progress-then-green edit was never published')
            self.assertTrue(self.wait_for(lambda: not list(pending.glob('*.json'))))
            self.assertTrue(self.wait_for(lambda: self.retry_worker_idle(pending)))
        subject = self.git(self.origin, 'show', '-s', '--format=%s', 'main')
        self.assertEqual(subject, 'skills: publish skills/testing/sample '
                                  '[session:session-desktop card:card-example]')
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assert_no_worktrees()

    def retry_worker_idle(self, pending):
        import fcntl
        lock = pending / '.worker.lock'
        if not lock.exists():
            return True
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def test_pending_retry_never_publishes_red_and_expires(self):
        bin_dir, state = self.fake_gh()
        pending = self.base / 'hermes-home/cache/skill-publish-pending'
        clock = [1000.0]
        with patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                                    'PUBLISH_CI_STATE': str(state)}):
            # In progress, then red: the record is dropped and nothing is published.
            state.write_text('in_progress null\n')
            publisher.defer(self.root, [self.path], session_id='s', task_id='c', spawn=False,
                            clock=lambda: clock[0])
            self.assertEqual(len(list(pending.glob('*.json'))), 1)
            self.assertEqual(publisher.retry_pending(pending, interval=0, clock=lambda: clock[0],
                                                     passes=1)['kept'], 1)
            state.write_text('completed failure\n')
            done = publisher.retry_pending(pending, interval=0, clock=lambda: clock[0], passes=1)
            self.assertEqual(done['dropped'], 1)
            self.assertEqual(list(pending.glob('*.json')), [])
            self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
            # Still in progress past the window: expired, never published.
            state.write_text('in_progress null\n')
            publisher.defer(self.root, [self.path], session_id='s', task_id='c', spawn=False,
                            clock=lambda: clock[0])
            clock[0] += publisher.RETRY_WINDOW + 1
            done = publisher.retry_pending(pending, interval=0, clock=lambda: clock[0], passes=1)
            self.assertEqual(done['expired'], 1)
            self.assertEqual(list(pending.glob('*.json')), [])
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assert_no_worktrees()

    def test_busy_publication_lock_is_deferred_then_published(self):
        import fcntl
        bin_dir, state = self.fake_gh()
        state.write_text('completed success\n')
        plugin, hooks = self.load_plugin('live_guard_lock_defer_test')
        holder = os.open(self.root / '.git/skill-publish.lock', os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        pending = self.base / 'hermes-home/cache/skill-publish-pending'
        with patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                                    'PUBLISH_CI_STATE': str(state),
                                    'HERMES_SKILL_PUBLISH_RETRY_INTERVAL': '0.5'}):
            with self.assertLogs(plugin.logger, level='INFO') as logs:
                hooks['post_tool_call'](tool_name='skill_manage', args={'action': 'write_file'},
                    result={'success': True, 'path': str(self.target)}, status='ok')
            self.assertIn('lock busy', logs.output[0])
            self.assertIn("'deferred'", logs.output[0])
            self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
            fcntl.flock(holder, fcntl.LOCK_UN)
            self.assertTrue(self.wait_for(
                lambda: self.git(self.origin, 'show', 'main:' + self.path) == 'published'))
            self.assertTrue(self.wait_for(lambda: not list(pending.glob('*.json'))))
            self.assertTrue(self.wait_for(lambda: self.retry_worker_idle(pending)))
        self.assert_no_worktrees()

    def retry_record(self):
        pending = self.base / 'hermes-home/cache/skill-publish-pending'
        publisher.defer(self.root, [self.path], session_id='s', task_id='c', spawn=False)
        records = list(pending.glob('*.json'))
        self.assertEqual(len(records), 1)
        return pending, records[0]

    def test_retry_keeps_record_while_publication_lock_busy_then_publishes(self):
        """A retry that finds the publication lock held keeps the record for the next pass."""
        import fcntl
        pending, record = self.retry_record()
        holder = os.open(self.root / '.git/skill-publish.lock', os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        green = lambda *_: 'green'
        with patch.object(publisher, 'LOCK_WAIT', 0.2):
            counts = publisher.retry_pending(pending, interval=0, ci=green, passes=1)
        self.assertTrue(record.exists(), 'record dropped while the publication lock was busy')
        self.assertEqual(counts['kept'], 1)
        self.assertEqual(counts['dropped'], 0)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
        self.assertEqual(self.target.read_text(), 'published\n')
        fcntl.flock(holder, fcntl.LOCK_UN)
        counts = publisher.retry_pending(pending, interval=0, ci=green, passes=1)
        self.assertEqual(counts['published'], 1)
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')
        self.assertFalse(record.exists())
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assert_no_worktrees()

    def test_retry_keeps_record_when_raced_tip_ci_pending_then_publishes(self):
        """A push race onto a tip whose CI is still running keeps the record for the next pass."""
        racer = self.base / 'racer'
        self.git(self.base, 'clone', '-q', '-b', 'main', str(self.origin), str(racer))
        self.git(racer, 'config', 'user.name', 'Fixture Racer')
        self.git(racer, 'config', 'user.email', 'racer@example.com')
        pending, record = self.retry_record()
        tip_state = ['pending']
        calls = []
        def ci(root, sha):
            calls.append(sha)
            return 'green' if sha == self.head else tip_state[0]
        with patch.dict(os.environ, {'PUBLISH_RACE_REPO': str(racer),
                                    'PUBLISH_RACE_SENTINEL': str(self.base / 'raced')}):
            counts = publisher.retry_pending(pending, interval=0, ci=ci, passes=1)
            # The race really happened and its new tip was the one read as pending.
            raced = self.git(self.origin, 'rev-parse', 'main')
            self.assertEqual(calls, [self.head, raced])
            self.assertEqual(self.git(self.origin, 'show', '-s', '--format=%s', 'main'), 'race')
            self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'base')
            self.assertTrue(record.exists(), 'record dropped while the raced tip CI was pending')
            self.assertEqual(counts['kept'], 1)
            self.assertEqual(counts['dropped'], 0)
            tip_state[0] = 'green'
            counts = publisher.retry_pending(pending, interval=0, ci=ci, passes=1)
        self.assertEqual(counts['published'], 1)
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main^'), raced)
        self.assertFalse(record.exists())
        # Published, so reconcile retired the live dirt (the edit now arrives by fast-forward).
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        # The deferred first pass left a commit that reached no remote: its worktree is kept. The publishing
        # pass's worktree is removed.
        self.assertEqual([kept is None for _, _, kept in self.reaped], [False, True])
        self.assertIn('custody refused: unpublished commit', self.reaped[0][2])
        self.assertEqual(self.git(self.root, 'worktree', 'list', '--porcelain').count('worktree '), 2)
        self.assertTrue(Path(self.reaped[0][0]).is_dir())
        self.assertFalse(Path(self.reaped[1][0]).exists())

    def test_pending_ci_state_is_distinct_from_unknown(self):
        def gh(rows, code=0):
            out = json.dumps(rows).encode()
            return patch.object(publisher, 'bounded', return_value=subprocess.CompletedProcess(
                ['gh'], code, out, b''))
        row = lambda status, conclusion, name='CI': {'headSha': 'a' * 40, 'status': status,
            'conclusion': conclusion, 'workflowName': name, 'createdAt': '2026-01-01'}
        cases = [([row('in_progress', None)], 'pending'),
                 ([row('queued', None)], 'pending'),
                 ([row('completed', 'success'), row('in_progress', None, 'Lint')], 'pending'),
                 ([row('completed', 'failure'), row('in_progress', None, 'Lint')], 'red'),
                 ([row('completed', 'skipped')], 'unknown'),
                 ([], 'unknown'),
                 ([row('completed', 'success')], 'green')]
        for rows, expected in cases:
            with self.subTest(rows=rows), gh(rows):
                self.assertEqual(publisher.latest_main_ci(self.root, 'a' * 40), expected)
        with gh([row('in_progress', None)], code=1):
            self.assertEqual(publisher.latest_main_ci(self.root, 'a' * 40), 'unknown')

    def test_cross_skill_batch_uses_separate_real_scope_checked_commits(self):
        other = 'skills/testing/another/SKILL.md'
        (self.root / other).parent.mkdir()
        (self.root / other).write_text('another skill\n')
        result = self.publish([self.path, other])
        self.assertEqual(result['status'], 'published')
        self.assertEqual(self.git(self.origin, 'rev-list', '--count', self.head + '..main'), '2')
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assert_no_worktrees()

    def test_real_core_compact_batch_rows_resolve_and_deduplicate(self):
        import sys
        core = Path(os.environ.get('HERMES_AGENT_DIR', Path.home() / '.hermes/hermes-agent'))
        if not (core / 'tools/skill_manager_tool.py').exists():
            self.skipTest('real core unavailable')
        self.target.write_text('---\nname: sample\ndescription: Use when testing.\n---\nDocumentation\n')
        home = self.base / 'home'
        (home / 'skills').mkdir(parents=True)
        (home / 'config.yaml').write_text(json.dumps({'skills': {'external_dirs': [str(self.root / 'skills')]}}))
        env = dict(os.environ, HERMES_HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, str(HERE / '_skill_publish_probe.py'),
                                 str(core), str(self.root)], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.path, 'skills/testing/sample/references/topic.md'])

    def test_external_alias_does_not_hide_internal_symlink(self):
        reference = self.target.parent / 'references'
        reference.mkdir()
        real = reference / 'real.md'
        real.write_text('document')
        alias = reference / 'alias.md'
        alias.symlink_to(real)
        external = self.base / 'external'
        external.symlink_to(self.target.parent, target_is_directory=True)
        result = {'success': True, 'path': str(external / 'references/alias.md')}
        with self.assertRaisesRegex(ValueError, 'symlink'):
            publisher.written_paths({'action': 'write_file'}, result, self.root)

    def test_stale_lock_file_does_not_block(self):
        (self.root / '.git/skill-publish.lock').write_text('stale from crashed process')
        self.assertEqual(self.publish()['status'], 'published')
        self.assertEqual(self.publish()['status'], 'unchanged')

    def test_held_flock_is_bounded_and_preserves_source(self):
        import fcntl
        holder = os.open(self.root / '.git/skill-publish.lock', os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        with patch.object(publisher, 'LOCK_WAIT', 0.2):
            with self.assertRaisesRegex(ValueError, 'lock busy'):
                self.publish()
        self.assertEqual(self.target.read_text(), 'published\n')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'main'), self.head)
        fcntl.flock(holder, fcntl.LOCK_UN)
        self.assertEqual(self.publish()['status'], 'published')

    def test_rebase_uses_checked_sha_not_moving_ref(self):
        racer = self.base / 'racer'
        self.git(self.base, 'clone', '-q', '-b', 'main', str(self.origin), str(racer))
        self.git(racer, 'config', 'user.name', 'Fixture Racer')
        self.git(racer, 'config', 'user.email', 'racer@example.com')
        calls = []
        def ci(root, sha):
            calls.append(sha)
            if len(calls) == 2:  # main moves again after the retry tip was fetched
                self.git(racer, 'pull', '-q', 'origin', 'main')
                self.git(racer, 'commit', '--allow-empty', '-qm', 'unchecked-late')
                self.git(racer, 'push', '-q', 'origin', 'HEAD:main')
                self.git(self.root, 'fetch', '-q', 'origin', 'main')  # unrelated fetch moves origin/main
            return 'green'
        with patch.dict(os.environ, {'PUBLISH_RACE_REPO': str(racer),
                                    'PUBLISH_RACE_SENTINEL': str(self.base / 'raced')}):
            self.assertEqual(self.publish(ci=ci)['status'], 'published')
        history = self.git(self.origin, 'rev-list', self.head + '..main').split()
        unchecked = [c for c in history if c not in calls and
                     self.git(self.origin, 'show', '-s', '--format=%s', c).startswith('skills: publish ') is False]
        self.assertEqual(unchecked, [])

    def test_inherited_git_routing_env_is_ignored(self):
        decoy = self.base / 'decoy'
        decoy.mkdir()
        with patch.dict(os.environ, {'GIT_DIR': str(decoy), 'GIT_WORK_TREE': str(decoy),
                                    'GIT_INDEX_FILE': str(decoy / 'index'),
                                    'GIT_COMMON_DIR': str(decoy),
                                    'GIT_OBJECT_DIRECTORY': str(decoy / 'objects'),
                                    'GIT_NAMESPACE': 'decoy',
                                    'GIT_CONFIG_COUNT': '1',
                                    'GIT_CONFIG_KEY_0': 'core.worktree',
                                    'GIT_CONFIG_VALUE_0': str(decoy)}):
            self.assertEqual(self.publish()['status'], 'published')
        self.assertEqual((self.root / '.git/index').read_bytes(), self.index)
        self.assertEqual(list(decoy.iterdir()), [])
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')

    def test_diff_ignores_external_diff_textconv_and_color(self):
        self.git(self.root, 'config', 'diff.external', 'false')
        self.git(self.root, 'config', 'color.ui', 'always')
        self.git(self.root, 'config', 'color.diff', 'always')
        self.git(self.root, 'config', 'diff.bad.textconv', 'false')
        (self.root / '.git/info').mkdir(exist_ok=True)
        (self.root / '.git/info/attributes').write_text('*.md diff=bad\n')
        with patch.dict(os.environ, {'GIT_EXTERNAL_DIFF': 'false'}):
            self.assertEqual(self.publish()['status'], 'published')
        self.assertEqual(self.git(self.origin, 'show', 'main:' + self.path), 'published')
        patch_file = next((self.base / 'hermes-home/kanban/evidence/mods-reconcile').glob('skill-publish-*/publish.patch'))
        self.assertNotIn(b'\x1b[', patch_file.read_bytes())

    def test_written_paths_resolves_symlinked_and_relative_root(self):
        link = self.base / 'live-link'
        link.symlink_to(self.root, target_is_directory=True)
        result = {'success': True, 'path': str(self.target)}
        self.assertEqual(publisher.written_paths({'action': 'write_file'}, result, link), [self.path])
        cwd = os.getcwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(publisher.written_paths({'action': 'write_file'}, result, 'live'), [self.path])

    def test_failed_or_staged_output_does_not_publish(self):
        for result in [{'success': False}, {'status': 'pending_approval'},
                       {'success': True, 'staged': True, 'pending_id': 'pending-example',
                        'path': str(self.target)}]:
            self.assertEqual(publisher.written_paths({'action': 'patch'}, result, self.root), [])


class CreatedOwnerTests(unittest.TestCase):
    """The owner marker written at `worktree add`, and the one removal route."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / 'repo'
        run = lambda cwd, *a: subprocess.run(['git', '-C', str(cwd), *a], check=True, capture_output=True, text=True,
                                             env=dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.com',
                                                      GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.com'))
        self.git = run
        run(self.base, 'init', '-q', '-b', 'main', str(self.root))
        run(self.root, 'commit', '-q', '--allow-empty', '-m', 'base')
        self.head = run(self.root, 'rev-parse', 'HEAD').stdout.strip()
        self.tree = self.base / 'archive' / 'worktree'
        self.tree.parent.mkdir()
        run(self.root, 'worktree', 'add', '-q', '--detach', str(self.tree), self.head)
        self.home = self.base / 'home'
        env = patch.dict(os.environ, {'HERMES_HOME': str(self.home), 'HOME': str(self.base / 'nohome')})
        env.start()
        self.addCleanup(env.stop)

    def marker(self):
        gitdir = self.git(self.tree, 'rev-parse', '--absolute-git-dir').stdout.strip()
        return Path(gitdir) / 'hermes-owner.json'

    def install_cli(self, body):
        cli = self.home / 'hermes-agent/.hermes/bin/hermes'
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text(body)
        cli.chmod(0o755)
        return cli

    def test_marker_is_the_created_class_with_a_random_id_and_is_never_overwritten(self):
        owner_id = publisher.mark_created(self.tree, self.head)
        rec = json.loads(self.marker().read_text())
        self.assertEqual(owner_id, 'created:skill-publish/' + rec['creation_id'])
        self.assertRegex(rec['creation_id'], r'^[0-9a-f]{32}$')
        self.assertEqual({k: rec[k] for k in ('tool', 'host', 'path', 'born_head')},
                         {'tool': 'skill-publish', 'host': os.uname().nodename.split('.')[0],
                          'path': os.path.realpath(self.tree), 'born_head': self.head})
        self.assertTrue(rec['registered_at'])
        # Core rejects a created marker that also carries task or session keys.
        self.assertFalse({'task_id', 'run_id', 'board_db', 'agent', 'agent_session_id'} & set(rec))
        self.assertEqual(self.marker().stat().st_mode & 0o777, 0o600)
        before = self.marker().read_bytes()
        self.assertIsNone(publisher.mark_created(self.tree, self.head))
        self.assertEqual(self.marker().read_bytes(), before)
        self.assertNotIn(rec['creation_id'], str(self.tree))  # the path confers nothing

    def test_remove_uses_the_exact_cli_argv_and_exit_0_alone_means_removed(self):
        record = self.base / 'argv'
        self.install_cli('#!/bin/sh\nprintf "%%s\\n" "$@" > %s\npwd >> %s\nexit "${CLI_EXIT:-0}"\n' % (record, record))
        self.assertIsNone(publisher.remove_created(self.tree, 'created:skill-publish/abc', time.monotonic))
        self.assertEqual(record.read_text().splitlines(),
                         ['kanban', 'source-custody', 'remove', '--kind', 'worktree', '--path', str(self.tree),
                          '--expected-owner', 'created:skill-publish/abc', '/'])
        for code in ('3', '1', '2'):
            with patch.dict(os.environ, {'CLI_EXIT': code}):
                kept = publisher.remove_created(self.tree, 'created:skill-publish/abc', time.monotonic)
            self.assertIn('source-custody remove exit ' + code, kept)
        self.assertTrue(self.tree.is_dir())

    def test_no_cli_no_owner_or_a_hung_cli_keeps_the_worktree_and_never_runs_git(self):
        with patch.object(publisher.subprocess, 'Popen') as popen:
            with patch.dict(os.environ, {'PATH': str(self.base / 'empty')}), \
                    patch.object(publisher, 'TOOL_DIRS', ()):
                self.assertIn('no installed hermes CLI',
                              publisher.remove_created(self.tree, 'created:skill-publish/abc', time.monotonic))
            self.assertIn('no created owner marker', publisher.remove_created(self.tree, None, time.monotonic))
            popen.assert_not_called()
        self.install_cli('#!/bin/sh\nsleep 30\n')
        with patch.object(publisher, 'REMOVE_BUDGET', 0.5):
            self.assertIn('did not finish', publisher.remove_created(self.tree, 'created:skill-publish/abc',
                                                                     time.monotonic))
        self.assertTrue(self.tree.is_dir())

    def test_reap_child_process_keeps_and_records_a_refused_worktree(self):
        self.install_cli('#!/bin/sh\necho "source-custody: custody refused: busy" >&2\nexit 3\n')
        done = subprocess.run([sys.executable, str(HERE / 'skill_publish.py'), '--reap', str(self.tree),
                               'created:skill-publish/abc'], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0)
        self.assertIn('kept publication worktree', done.stderr)
        note = (self.tree.parent / 'worktree-kept.txt').read_text()
        self.assertIn('custody refused: busy', note)
        self.assertIn('custody refused: busy', (self.home / 'logs/skill-publish-retry.log').read_text())
        self.assertTrue(self.tree.is_dir())


if __name__ == '__main__':
    unittest.main()

"""Real bare-origin publication fixtures; only the external CI service is injected."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location('skill_publish_test', HERE / 'skill_publish.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('gitleaks'):
            self.skipTest('real pre-commit tests require gitleaks')
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
        self.assertEqual(self.git(self.root, 'worktree', 'list', '--porcelain').count('worktree '), 1)
        self.assertFalse(self.git(self.root, 'worktree', 'list').count('skill-publish-') > 0)

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
        self.assert_no_worktrees()

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
        self.assert_no_worktrees()

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
                                    'GIT_NAMESPACE': 'decoy'}):
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


if __name__ == '__main__':
    unittest.main()

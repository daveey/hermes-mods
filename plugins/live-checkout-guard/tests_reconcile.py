"""Real local repositories only; never touch the default live root."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('reconcile.py')


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        (self.root / 'file').write_bytes(b'base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.head = self.git('rev-parse', 'HEAD').strip()
        (self.root / 'file').write_bytes(b'published\n')
        self.git('commit', '-qam', 'published')
        self.published = self.git('rev-parse', 'HEAD').strip()
        self.git('update-ref', 'refs/remotes/origin/main', self.published)
        self.git('remote', 'add', 'origin', str(self.base / 'origin.git'))
        (self.root / 'file').write_bytes(b'archived\n')
        self.archive = self.base / 'archive'
        self.archive.mkdir()
        self.make_archive()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    def make_archive(self, untracked=()):
        (self.archive / 'manifest.json').write_text(json.dumps({
            'head': self.published, 'origin': self.git('remote', 'get-url', 'origin').strip(),
            'status': self.git('status', '--porcelain'), 'untracked': list(untracked)}))
        (self.archive / 'changes.patch').write_bytes(subprocess.check_output([
            'git', '-C', str(self.root), 'diff', '--binary', 'HEAD']))
        with tarfile.open(self.archive / 'untracked.tar.gz', 'w:gz') as tar:
            for name in untracked:
                tar.add(self.root / name, arcname=name)

    def run_helper(self, *paths, published=None):
        cmd = [sys.executable, str(SCRIPT), '--root', str(self.root),
               '--archive', str(self.archive), '--published', published or self.published]
        for path in paths:
            cmd += ['--path', path]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_archive_origin_commit_is_validated_against_published_history(self):
        p = self.archive / 'manifest.json'
        manifest = json.loads(p.read_text())
        manifest['origin'] = self.head
        p.write_text(json.dumps(manifest))
        result = self.run_helper('file')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reconciliation_on_older_live_head_leaves_clean_ffable_tree(self):
        self.git('checkout', '-q', '-f', '--detach', self.head)
        (self.root / 'file').write_bytes(b'archived\n')
        self.make_archive()
        manifest_path = self.archive / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['head'] = self.head
        manifest_path.write_text(json.dumps(manifest))
        result = self.run_helper('file')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.git('merge', '--ff-only', 'origin/main')
        self.assertEqual((self.root / 'file').read_bytes(), b'published\n')

    def test_restores_published_bytes_without_index_or_head_changes(self):
        before_head = self.git('rev-parse', 'HEAD')
        before_index = (self.root / '.git/index').read_bytes()
        result = self.run_helper('file')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'file').read_bytes(), b'published\n')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before_head)
        self.assertEqual((self.root / '.git/index').read_bytes(), before_index)
        records = (self.archive / 'reconcile-audit.jsonl').read_text().splitlines()
        self.assertEqual(json.loads(records[-1])['paths'], ['file'])


    def test_changed_current_refuses_all_selected_paths(self):
        (self.root / 'extra').write_bytes(b'archived extra')
        self.make_archive(['extra'])
        (self.root / 'extra').write_bytes(b'new writer')
        result = self.run_helper('file', 'extra')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'file').read_bytes(), b'archived\n')
        self.assertEqual((self.root / 'extra').read_bytes(), b'new writer')

    def test_unpublished_commit_refused(self):
        self.git('add', 'file')
        self.git('commit', '-qm', 'not published')
        unpublished = self.git('rev-parse', 'HEAD').strip()
        result = self.run_helper('file', published=unpublished)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'file').read_bytes(), b'archived\n')

    def test_path_traversal_refused(self):
        outside = self.base / 'outside'
        outside.write_bytes(b'untouched')
        for path in ['../outside', str(outside), '.git/config', 'file/../file']:
            with self.subTest(path=path):
                self.assertNotEqual(self.run_helper(path).returncode, 0)
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_symlink_escape_refused(self):
        outside = self.base / 'outside'
        outside.write_bytes(b'archived\n')
        (self.root / 'file').unlink()
        (self.root / 'file').symlink_to(outside)
        self.assertNotEqual(self.run_helper('file').returncode, 0)
        self.assertEqual(outside.read_bytes(), b'archived\n')

    def test_untracked_archived_file_removed_when_published_absent(self):
        (self.root / 'extra').write_bytes(b'only archived')
        self.make_archive(['extra'])
        result = self.run_helper('extra')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'extra').exists())
        self.assertEqual((self.root / 'file').read_bytes(), b'archived\n')

    def test_clean_path_is_not_archived_dirt(self):
        result = self.run_helper('missing')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not archived dirt', result.stderr)

    def test_corrupt_patch_refused(self):
        (self.archive / 'changes.patch').write_bytes(b'not a patch\n')
        result = self.run_helper('file')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'file').read_bytes(), b'archived\n')

    def test_helper_lock_refuses_concurrent_reconciler(self):
        (self.root / '.git/reconcile.lock').write_text('busy')
        self.assertNotEqual(self.run_helper('file').returncode, 0)
        self.assertEqual((self.root / 'file').read_bytes(), b'archived\n')

    def test_tar_traversal_refused_before_any_write(self):
        import io
        with tarfile.open(self.archive / 'untracked.tar.gz', 'w:gz') as tar:
            entry = tarfile.TarInfo('../escaped')
            entry.size = 4
            tar.addfile(entry, io.BytesIO(b'evil'))
        self.assertNotEqual(self.run_helper('file').returncode, 0)
        self.assertFalse((self.base / 'escaped').exists())
        self.assertEqual((self.root / 'file').read_bytes(), b'archived\n')

    def test_audit_symlink_refused_before_write(self):
        outside = self.base / 'outside-audit'
        outside.write_text('untouched')
        (self.archive / 'reconcile-audit.jsonl').symlink_to(outside)
        result = self.run_helper('file')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'file').read_bytes(), b'archived\n')
        self.assertEqual(outside.read_text(), 'untouched')

    def test_binary_snapshot_reconstruction(self):
        (self.root / 'file').write_bytes(b'\x00archived\xff')
        self.make_archive()
        result = self.run_helper('file')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'file').read_bytes(), b'published\n')

    def test_current_type_change_refused(self):
        (self.root / 'file').unlink()
        (self.root / 'file').mkdir()
        self.assertNotEqual(self.run_helper('file').returncode, 0)
        self.assertTrue((self.root / 'file').is_dir())

    def test_new_writer_during_validation_refused(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('reconcile_under_test', SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original_git = module.git

        def concurrent_git(root, *args):
            output = original_git(root, *args)
            if args and args[0] == 'ls-tree':
                (self.root / 'file').write_bytes(b'new concurrent writer')
            return output

        with patch.object(module, 'git', side_effect=concurrent_git):
            with self.assertRaisesRegex(ValueError, 'new writer'):
                module.reconcile(self.root, self.archive, self.published, ['file'])
        self.assertEqual((self.root / 'file').read_bytes(), b'new concurrent writer')

    def test_manifest_requires_status(self):
        manifest = json.loads((self.archive / 'manifest.json').read_text())
        del manifest['status']
        (self.archive / 'manifest.json').write_text(json.dumps(manifest))
        self.assertNotEqual(self.run_helper('file').returncode, 0)


if __name__ == '__main__':
    unittest.main()

"""Regression coverage for directory loss and pre-verdict staging exposure."""
import importlib.util
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import Base, core, SNAP, UPLOAD


class SafetyTests(Base):
    def test_directory_write_refused_without_grant(self):
        self._directory_refused()

    def test_directory_write_refused_with_ancestor_grant(self):
        self.grants.add('plugin_rule:' + core.rule_key_for('write', str(self.outdir.parent)))
        self._directory_refused()

    def _directory_refused(self):
        (self.outdir / 'valuable').write_text('keep')
        args = {'filePath': str(self.outdir)}
        d = self.gate(SNAP, args)
        self.assertEqual(d['action'], 'block')
        self.assertIsNone(self.prepare(SNAP, args))
        core.finish(tool_name=SNAP, tool_call_id='c1')
        self.assertEqual((self.outdir / 'valuable').read_text(), 'keep')

    def test_directory_appearing_after_prepare_survives(self):
        dest = self.outdir / 'later'
        args = {'filePath': str(dest)}
        self.gate(SNAP, args)
        p = self.prepare(SNAP, args)
        Path(p['args']['filePath']).write_text('output')
        dest.mkdir()
        (dest / 'valuable').write_text('keep')
        with self.assertRaises(IsADirectoryError):
            core.finish(tool_name=SNAP, tool_call_id='c1')
        self.assertEqual((dest / 'valuable').read_text(), 'keep')
        self.assertTrue(self.staging_empty())

    def test_private_staging_under_permissive_umask_and_denial_cleanup(self):
        args = {'filePaths': [str(self.photo)]}
        self.photo.chmod(0o644)
        self.gate(UPLOAD, args)
        old = os.umask(0)
        try:
            p = self.prepare(UPLOAD, args)
        finally:
            os.umask(old)
        staged = Path(p['args']['filePaths'][0])
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o600)
        for directory in (staged.parent, staged.parent.parent, staged.parent.parent.parent):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        core.finish(tool_name=UPLOAD, tool_call_id='c1')
        self.assertFalse(staged.exists())

    def test_startup_removes_orphans_but_preserves_live_stages(self):
        args = {'filePaths': [str(self.photo)]}
        self.gate(UPLOAD, args)
        p = self.prepare(UPLOAD, args)
        live = Path(p['args']['filePaths'][0])
        script = '''import importlib.util, sys
s = importlib.util.spec_from_file_location('isolated_core', sys.argv[1])
c = importlib.util.module_from_spec(s); s.loader.exec_module(c)
getattr(c, 'cleanup_staging', lambda **kw: None)(_tmpdir=sys.argv[2])
'''
        core_path = str(Path(core.__file__).resolve())
        subprocess.run([sys.executable, '-c', script, core_path, str(self.server_tmp)], check=True)
        self.assertTrue(live.exists())
        orphan = self.server_tmp / core._STAGE_DIRNAME / ('a' * 32)
        orphan.mkdir()
        (orphan / 'secret').write_text('orphan')
        subprocess.run([sys.executable, '-c', script, core_path, str(self.server_tmp)], check=True)
        self.assertFalse(orphan.exists())
        self.assertTrue(live.exists())
        core.finish(tool_name=UPLOAD, tool_call_id='c1')

    def test_directory_created_at_atomic_publication_survives(self):
        dest = self.outdir / 'raced'
        args = {'filePath': str(dest)}
        self.gate(SNAP, args)
        p = self.prepare(SNAP, args)
        Path(p['args']['filePath']).write_text('output')
        replace = os.replace

        def race(source, target):
            dest.mkdir()
            (dest / 'valuable').write_text('keep')
            return replace(source, target)

        with patch.object(core.os, 'replace', side_effect=race):
            with self.assertRaises(IsADirectoryError):
                core.finish(tool_name=SNAP, tool_call_id='c1')
        self.assertEqual((dest / 'valuable').read_text(), 'keep')
        self.assertEqual(list(self.outdir.iterdir()), [dest])

    def test_non_file_output_is_refused(self):
        args = {'filePath': str(self.outdir / 'bad')}
        self.gate(SNAP, args)
        p = self.prepare(SNAP, args)
        Path(p['args']['filePath']).mkdir()
        with self.assertRaises(ValueError):
            core.finish(tool_name=SNAP, tool_call_id='c1')
        self.assertFalse((self.outdir / 'bad').exists())

    def test_symlink_output_is_refused(self):
        args = {'filePath': str(self.outdir / 'bad')}
        self.gate(SNAP, args)
        p = self.prepare(SNAP, args)
        Path(p['args']['filePath']).symlink_to(self.photo)
        with self.assertRaises(ValueError):
            core.finish(tool_name=SNAP, tool_call_id='c1')
        self.assertFalse((self.outdir / 'bad').exists())

    def test_foreign_owned_staging_root_refused(self):
        args = {'filePaths': [str(self.photo)]}
        self.gate(UPLOAD, args)
        uid = os.getuid()
        with patch.object(core.os, 'getuid', return_value=uid + 1):
            with self.assertRaises(PermissionError):
                self.prepare(UPLOAD, args)

    def test_staging_root_symlink_refused(self):
        root = self.server_tmp / core._STAGE_DIRNAME
        root.symlink_to(self.outdir, target_is_directory=True)
        args = {'filePaths': [str(self.photo)]}
        self.gate(UPLOAD, args)
        with self.assertRaises(OSError):
            self.prepare(UPLOAD, args)
        self.assertEqual(list(self.outdir.iterdir()), [])

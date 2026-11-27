"""Exact-head review regressions (run against the reviewed implementation too)."""
import os
import stat
from pathlib import Path
from unittest.mock import patch

from test_core import Base, core, SNAP, UPLOAD


class ReviewTests(Base):
    def stage_read(self):
        args = {'filePaths': [str(self.photo)]}
        self.gate(UPLOAD, args)
        return self.prepare(UPLOAD, args)

    def test_finish_deletes_under_root_lock(self):
        self.stage_read()
        real_root = core._stage_root
        real_rmtree = core.shutil.rmtree
        held = []
        from contextlib import contextmanager

        @contextmanager
        def tracked(tmpdir):
            with real_root(tmpdir) as root:
                held.append(root)
                try:
                    yield root
                finally:
                    held.pop()

        def remove(path, *args, **kwargs):
            self.assertTrue(held, 'stage deletion must hold root lock')
            return real_rmtree(path, *args, **kwargs)

        with patch.object(core, '_stage_root', tracked), patch.object(core.shutil, 'rmtree', remove):
            core.finish(tool_name=UPLOAD, tool_call_id='c1')

    def test_reaper_tolerates_vanished_stage(self):
        root = self.server_tmp / core._STAGE_DIRNAME
        root.mkdir()
        vanished = root / ('a' * 32)
        vanished.mkdir()
        real_open = os.open

        def disappear(path, *args, **kwargs):
            if Path(path) == vanished / '.lease':
                vanished.rmdir()
            return real_open(path, *args, **kwargs)

        with patch.object(core.os, 'open', side_effect=disappear):
            try:
                core._reap_orphans(root)
            except FileNotFoundError:
                self.fail('vanished orphan must not fail cleanup')

    def test_startup_cleanup_is_best_effort(self):
        with patch.object(core, '_reap_orphans', side_effect=PermissionError('fixture')):
            try:
                core.cleanup_staging(_tmpdir=str(self.server_tmp))
            except OSError:
                self.fail('startup cleanup must not prevent plugin registration')

    def test_publication_preserves_staged_file_mode_for_new_and_existing(self):
        for existing in (False, True):
            dest = self.outdir / str(existing)
            if existing:
                dest.write_text('old')
                dest.chmod(0o644)
            args = {'filePath': str(dest)}
            self.gate(SNAP, args, str(existing))
            staged = Path(self.prepare(SNAP, args, str(existing))['args']['filePath'])
            staged.write_text('new')
            staged.chmod(0o644)
            core.finish(tool_name=SNAP, tool_call_id=str(existing))
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o644)
            self.assertEqual(dest.read_text(), 'new')

    def test_refused_output_does_not_drop_sibling_or_claim_delivery(self):
        tool = 'mcp__chrome_devtools__get_network_request'
        bad, good = self.outdir / 'bad', self.outdir / 'good'
        args = {'requestFilePath': str(bad), 'responseFilePath': str(good)}
        self.gate(tool, args)
        staged = self.prepare(tool, args)['args']
        for value in staged.values():
            Path(value).write_text('output')
        bad.mkdir()
        with self.assertRaises(IsADirectoryError):
            core.finish(tool_name=tool, tool_call_id='c1')
        self.assertTrue(good.exists(), 'other output must still be delivered')
        result = core.rewrite_result(tool_name=tool, tool_call_id='c1',
                                     result='Saved ' + staged['requestFilePath'] + ' and ' + staged['responseFilePath'])
        self.assertNotIn('Saved ' + str(bad), result)
        self.assertIn('not delivered', result)

    def test_transform_before_post_reports_refusal_and_delivers_sibling(self):
        tool = 'mcp__chrome_devtools__get_network_request'
        bad, good = self.outdir / 'bad', self.outdir / 'good'
        args = {'requestFilePath': str(bad), 'responseFilePath': str(good)}
        self.gate(tool, args)
        staged = self.prepare(tool, args)['args']
        for value in staged.values():
            Path(value).write_text('output')
        bad.mkdir()
        result = core.rewrite_result(tool_name=tool, tool_call_id='c1',
                                     result='Saved ' + staged['requestFilePath'] + ' and ' + staged['responseFilePath'])
        core.finish(tool_name=tool, tool_call_id='c1')
        self.assertTrue(good.exists())
        self.assertNotIn('Saved ' + str(bad), result)
        self.assertIn('not delivered', result)
        self.assertEqual(core._staged, {})

    def test_finish_concurrent_with_prepare_and_cleanup(self):
        from concurrent.futures import ThreadPoolExecutor

        def calls(worker):
            for index in range(30):
                call_id = f'{worker}-{index}'
                args = {'filePaths': [str(self.photo)]}
                self.gate(UPLOAD, args, call_id)
                self.prepare(UPLOAD, args, call_id)
                core.finish(tool_name=UPLOAD, tool_call_id=call_id)
                core.rewrite_result(tool_name=UPLOAD, tool_call_id=call_id, result='done')

        def reap():
            for _ in range(120):
                core.cleanup_staging(_tmpdir=str(self.server_tmp))

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(calls, worker) for worker in range(4)]
            futures += [pool.submit(reap) for _ in range(2)]
            for future in futures:
                future.result(timeout=30)
        self.assertTrue(self.staging_empty())
        self.assertEqual(core._staged, {})

    def test_missing_tmpdir_parent_is_created(self):
        self.server_tmp = self.server_tmp / 'missing' / 'nested'
        try:
            result = self.stage_read()
        except FileNotFoundError:
            self.fail('configured TMPDIR parent should be created')
        self.assertTrue(Path(result['args']['filePaths'][0]).exists())

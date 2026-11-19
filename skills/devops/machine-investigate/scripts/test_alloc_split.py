"""Focused allocator-summary contracts; fixture values are synthetic, not live measurements."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('alloc_split', Path(__file__).with_name('alloc_split.py'))
assert spec is not None and spec.loader is not None
alloc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alloc)
SAMPLE = '''Physical footprint: 512M
Untagged 1G 90M 80M 20M 1
Malloc Large (empty) 2G 9M 8M 2M 1
Malloc Small (empty) 1G 5M 4M 1M 1
TOTAL 4G 200M 150M 50M 123 120M 30M 20% 9
'''

class AllocSplitTests(unittest.TestCase):
    def test_summary(self):
        with patch.object(alloc.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, SAMPLE, '')):
            out = alloc.split('123')
        self.assertIn('footprint 512 MB', out)
        self.assertIn('untagged dirty+swapped 100', out)
        self.assertIn('malloc in use 120 (123 blocks)', out)
        self.assertIn('fragmentation 30 + empty regions 15', out)

    def test_failed_vmmap_is_not_zero(self):
        with patch.object(alloc.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'vmmap')):
            with self.assertRaises(subprocess.CalledProcessError):
                alloc.split('123')

    def test_missing_tables_are_not_zero(self):
        for text in ('', 'Physical footprint: 1G\n'):
            with self.subTest(text=text), patch.object(alloc.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, text, '')):
                with self.assertRaises(ValueError): alloc.split('123')

    def test_units(self):
        for token, expected in [('1G',1024),('1024K',1),('1048576B',1),('0K',0),('1.5M',1.5)]:
            self.assertEqual(alloc.mb(token), expected)
        with self.assertRaises(ValueError): alloc.mb('unknown')

    def test_invalid_pid(self):
        with patch.object(alloc.subprocess, 'run') as run:
            with self.assertRaises(ValueError): alloc.split('--help')
            run.assert_not_called()

    def test_bounded_checked_command(self):
        with patch.object(alloc.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, SAMPLE, '')) as run:
            alloc.split('123')
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertTrue(run.call_args.kwargs['check'])

if __name__ == '__main__': unittest.main()

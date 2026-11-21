"""custody_remove: the one place the plugin asks core to remove a worktree.

Run: python3 -B -m unittest discover -s plugins/worktree-owner/tests   (stdlib only)

A recording launcher stands in for the installed `hermes`: these tests pin the argv, the environment and the
handling of each exit code. The removal itself is core's and is tested there."""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_worktree_owner import load

LAUNCHER = """#!/bin/sh
{
  printf 'ARGV'; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'
  printf 'DB\\t%s\\n' "${HERMES_KANBAN_DB-unset}"
  printf 'TASK\\t%s\\n' "${HERMES_KANBAN_TASK-unset}"
  printf 'BOARD\\t%s\\n' "${HERMES_KANBAN_BOARD-unset}"
  printf 'CWD\\t%s\\n' "$(pwd)"
} >> "$CUSTODY_RECORD"
[ -n "$CUSTODY_SLEEP" ] && sleep "$CUSTODY_SLEEP"
[ -n "$CUSTODY_STDERR" ] && printf '%s\\n' "$CUSTODY_STDERR" >&2
exit "${CUSTODY_EXIT:-0}"
"""


class CustodyCli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.m = load()
        self.remove = self.m._real_custody_remove
        self.cli = self.root / 'hermes'
        self.cli.write_text(LAUNCHER)
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)
        self.record = self.root / 'record'
        self.tree = self.root / 'tree'
        self.tree.mkdir()
        self.env = dict(os.environ, CUSTODY_RECORD=str(self.record))
        for name in ('CUSTODY_EXIT', 'CUSTODY_STDERR', 'CUSTODY_SLEEP'):
            self.env.pop(name, None)
        found = patch.object(self.m, '_custody_cli', return_value=str(self.cli))
        found.start()
        self.addCleanup(found.stop)

    def run_remove(self, owner='task:t_1@7', db=None, **extra):
        with patch.dict(os.environ, dict(self.env, **extra), clear=True):
            return self.remove(str(self.tree), owner, db)

    def recorded(self):
        return dict(line.split('\t', 1) for line in self.record.read_text().splitlines())

    def test_exit_0_is_the_only_removed_answer_and_the_argv_is_exact(self):
        self.assertIsNone(self.run_remove())
        self.assertEqual(self.recorded()['ARGV'].split('\t'),
                         ['kanban', 'source-custody', 'remove', '--kind', 'worktree', '--path', str(self.tree),
                          '--expected-owner', 'task:t_1@7'])
        self.assertEqual(self.recorded()['CWD'], '/')

    def test_exit_3_and_exit_1_keep_the_tree_with_cores_reason(self):
        for code, text in ((3, 'source-custody: custody refused: busy'), (1, 'source-custody: owner marker is a different owner'),
                           (2, 'usage: hermes kanban source-custody'), (70, '')):
            with self.subTest(code=code):
                kept = self.run_remove(CUSTODY_EXIT=str(code), CUSTODY_STDERR=text)
                self.assertTrue(kept.startswith(f'source-custody remove exit {code}: '))
                self.assertIn(text or 'no detail', kept)

    def test_no_owner_id_never_starts_the_cli(self):
        for owner in (None, ''):
            self.assertEqual(self.run_remove(owner=owner), 'no owner evidence to remove it with')
        self.assertFalse(self.record.exists())

    def test_missing_cli_keeps_the_tree_and_there_is_no_git_fallback(self):
        with patch.object(self.m, '_custody_cli', return_value=None), patch.object(self.m, '_git') as git_call, \
                patch.object(self.m.subprocess, 'run') as run:
            self.assertIn('no installed hermes CLI', self.run_remove())
            git_call.assert_not_called()
            run.assert_not_called()

    def test_unstartable_cli_and_timeout_keep_the_tree(self):
        with patch.object(self.m, '_custody_cli', return_value=str(self.root / 'absent')):
            self.assertIn('could not start', self.run_remove())
        with patch.object(self.m, 'CUSTODY_TIMEOUT_S', 0.3):
            self.assertIn('gave no answer', self.run_remove(CUSTODY_SLEEP='5'))

    def test_the_board_is_the_callers_and_worker_pins_do_not_leak(self):
        pins = dict(HERMES_KANBAN_DB='/worker/own.db', HERMES_KANBAN_TASK='t_worker', HERMES_KANBAN_BOARD='other')
        db = self.root / 'board.db'
        db.write_text('')
        self.assertIsNone(self.run_remove(db=str(db), **pins))
        self.assertEqual(self.recorded(), dict(self.recorded(), DB=str(db), TASK='unset', BOARD='unset'))
        self.record.unlink()
        self.assertIsNone(self.run_remove(owner='session:claude/s1', **pins))  # no board: core must not see one
        self.assertEqual(self.recorded()['DB'], 'unset')

    def test_cli_lookup_prefers_the_installed_core_launcher(self):
        home = self.root / 'home'
        launcher = home / '.hermes/hermes-agent/.hermes/bin/hermes'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\n')
        m = load()
        with patch.object(m, 'H', home), patch.object(m.shutil, 'which', return_value='/usr/bin/hermes'):
            self.assertEqual(m._custody_cli(), '/usr/bin/hermes')  # not executable: fall back to PATH
            launcher.chmod(0o755)
            self.assertEqual(m._custody_cli(), str(launcher))
        with patch.object(m, 'H', self.root / 'nohome'), patch.object(m.shutil, 'which', return_value=None):
            self.assertIsNone(m._custody_cli())


if __name__ == '__main__':
    unittest.main()

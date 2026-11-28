"""Successor source findings: wholly inert process and transport adapters."""
import os
import shlex
import subprocess
from unittest import mock

from test_guard import Base

SSH = 's' + 'sh'
PKILL = 'p' + 'kill'
A, B = 't_aaa111', 't_bbb222'


class Successor(Base):
    def local(self, approvals='', second=B, output=None, rc=0, bounded=False):
        collect, lib = self.g.libs()
        rows = output if output is not None else ('4242 1 0 1024 00:10 fixturejob\n'
                                                  '4243 1 0 1024 00:10 fixturejob\n')
        def tags(pids, **kwargs):
            if second is None:
                raise PermissionError('second owner unreadable')
            return {4242: {'HERMES_OWNER_CARD': A},
                    4243: {'HERMES_OWNER_CARD': second}}
        result = subprocess.CompletedProcess(['ps'], rc, rows, '')
        result.truncated = bounded
        with mock.patch.object(self.g, '_pinned', return_value=('', '')), \
             mock.patch.object(subprocess, 'run', return_value=result), \
             mock.patch.object(collect, 'env_tags', side_effect=tags), \
             mock.patch.object(lib, '_cwd_of', return_value=str(self.home)):
            return self.g.check(PKILL + ' -x fixturejob # ' + approvals)

    def test_mixed_approval(self):
        self.assertIsNotNone(self.local('owner-approved=' + A))

    def test_approval_controls(self):
        self.assertIsNotNone(self.local())
        self.assertIsNone(self.local('owner-approved=' + A + ' owner-approved=' + B))
        self.assertIsNone(self.local('owner-approved=' + A,
                                     output='4242 1 0 1024 00:10 fixturejob\n'))

    def test_incomplete_observations(self):
        for extra in [dict(second=None), dict(rc=1), dict(bounded=True),
                      dict(output='4242 1 0 1024 00:10 fixturejob\nunreadable row\n')]:
            with self.subTest(extra=extra):
                self.assertIsNotNone(self.local('owner-approved=' + A, **extra))

    def test_reader_errors_are_not_silent_absence(self):
        collect, lib = self.g.libs()
        with mock.patch.object(collect, 'MAC', False), \
             mock.patch('pathlib.Path.read_bytes', side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):
                collect.env_tags([4242], strict=True)
        with mock.patch('pathlib.Path.open', side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):
                collect.read_owner_md(self.home, strict=True)
        table = collect.parse_ps('4242 1 0 1024 00:10 fixturejob\n')
        with self.assertRaises(OSError):
            collect.owner(table, {}, 4242, strict=True)
        with mock.patch.object(collect, 'MAC', True), \
             mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess(
                 ['ps'], 0, '4242 fixturejob\n', '')):
            with self.assertRaises(OSError):
                collect.env_tags([4242, 4243], strict=True)

    def test_local_listing_limits(self):
        for output in ['4242 1 0 1024 00:10 fixturejob\n' * 8192,
                       '4242 1 0 1024 00:10 ' + 'x' * (4 * 1024 * 1024)]:
            self.assertIsNotNone(self.local('owner-approved=' + A, output=output))

    def remote(self, text):
        with mock.patch.object(self.g, 'remote_lookup', return_value={
                'status': 'found', 'owner': A}) as lookup:
            verdict = self.g.check(text)
            return verdict, lookup.call_args_list

    def test_post_destination_port(self):
        before, calls = self.remote(SSH + " -p 2222 box 'systemctl stop foo'")
        after, after_calls = self.remote(SSH + " box -p 2222 'systemctl stop foo'")
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertEqual(calls, after_calls)
        self.assertEqual(len(calls), 1)

    def test_all_allowlisted_options_same_before_after(self):
        options = ['-' + f for f in '46aACknqTtvXxyNf'] + [
            '-i /fixture/key', '-l you', '-p 2222', '-o Port=2222',
            '-o User=you', '-o IdentityFile=/fixture/key', '-o IdentitiesOnly=yes',
            '-o BatchMode=yes', '-o ConnectTimeout=1', '-vp 2222', '-p2222']
        for opts in options:
            with self.subTest(opts=opts):
                before, calls = self.remote(SSH + f" {opts} box 'systemctl stop foo'")
                after, later = self.remote(SSH + f" box {opts} 'systemctl stop foo'")
                self.assertIsNotNone(before)
                self.assertIsNotNone(after)
                self.assertEqual(calls, later)
                self.assertEqual(len(calls), 1)

    def test_unsupported_after_destination(self):
        for opts in ['-P tag', '-Z', '-- -p 2222', '-F cfg', '-oProxyCommand=bad']:
            with self.subTest(opts=opts):
                verdict, calls = self.remote(SSH + f" box {opts} 'systemctl stop foo'")
                self.assertIsNotNone(verdict)
                self.assertEqual(calls, [])

    def test_non_actions(self):
        for command in ['systemctl status foo', 'uptime']:
            verdict, calls = self.remote(SSH + ' box ' + shlex.quote(command))
            self.assertIsNone(verdict)
            self.assertEqual(calls, [])

"""Kernel-UID ownership boundaries keep walking, never infer trust from names."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from test_guard import Base


class LinuxUidChain(Base):
    def fixture(self, mode='ssh', tagged_above=False, target_denied=False,
                readable_ancestor_owner=False, broken_status=False, change_birth=False):
        collect, lib = self.g.libs()
        proc = self.home / 'proc'
        proc.mkdir()
        uid = os.getuid()
        # SSH-like: same-UID session child of root daemon; an independent
        # readable owner above it must still be considered after exclusions.
        rows = [(4242, 4200, uid), (4200, 4100, 0 if mode == 'foreign' else uid),
                (4100, 4000, 0 if mode == 'ssh' else uid), (4000, 1, uid)]
        for pid, ppid, real_uid in rows:
            path = proc / str(pid)
            path.mkdir()
            fields = ['S', str(ppid)] + ['0'] * 17 + ['12345'] + ['0'] * 5
            (path / 'stat').write_text(str(pid) + ' (systemd) ' + ' '.join(fields))
            (path / 'status').write_text('Uid:\t' + '\t'.join([str(real_uid)] * 4) + '\n')
            (path / 'cgroup').write_text('0::/ordinary-session\n')
            marked = (tagged_above and pid == 4000) or (readable_ancestor_owner and pid == 4200)
            (path / 'environ').write_bytes(b'HERMES_OWNER_CARD=t_ccdd' if marked else b'')
        if broken_status:
            (proc / '4200/status').write_text('Uid: unreadable\n')
        denied = {4200} | ({4100} if mode == 'ssh' else set()) | ({4242} if target_denied else set())
        real_read = Path.read_bytes
        def read_bytes(path):
            if str(path).startswith('/proc/') and path.name == 'environ':
                pid = int(path.parent.name)
                if pid == 4200 and change_birth:
                    stat = proc / str(pid) / 'stat'
                    stat.write_text(stat.read_text().replace('12345', '12346'))
                if pid in denied and not (pid == 4200 and readable_ancestor_owner):
                    raise PermissionError('fixture nondumpable ancestor')
                return real_read(proc / str(pid) / 'environ')
            return real_read(path)
        output = ''.join(f'{pid} {ppid} 0 1024 00:10 systemd\n' for pid, ppid, uid in rows)
        def run(argv, **kwargs):
            if argv[:2] == ['ps', '-Ao']:
                return subprocess.CompletedProcess(argv, 0, output, '')
            raise AssertionError('unexpected external command: ' + repr(argv))
        with mock.patch.object(self.g, 'PROC', proc), \
             mock.patch.object(self.g.platform, 'system', return_value='Linux'), \
             mock.patch.object(collect, 'MAC', False), \
             mock.patch.object(Path, 'read_bytes', read_bytes), \
             mock.patch.object(subprocess, 'run', run), \
             mock.patch.object(self.g, '_pinned', return_value=('', '')), \
             mock.patch.object(lib, '_cwd_of', side_effect=lambda pid, run: '' if pid in denied else str(self.home)):
            return self.g.check('kill 4242')

    def test_ssh_like_chain_without_manager_is_available(self):
        self.assertIsNone(self.fixture())

    def test_foreign_uid_unreadable_ancestor_is_not_service_owner(self):
        self.assertIsNone(self.fixture(mode='foreign'))

    def test_foreign_uid_skip_keeps_tagged_ancestor_above(self):
        result = self.fixture(mode='foreign', tagged_above=True)
        self.assertIsNotNone(result)
        self.assertIn('t_ccdd', result)

    def test_privileged_parent_skip_keeps_tagged_ancestor_above(self):
        result = self.fixture(tagged_above=True)
        self.assertIsNotNone(result)
        self.assertIn('t_ccdd', result)

    def test_readable_owner_on_uid_excluded_candidate_still_refuses(self):
        result = self.fixture(mode='foreign', readable_ancestor_owner=True)
        self.assertIsNotNone(result)
        self.assertIn('t_ccdd', result)

    def test_ambiguous_uid_evidence_stays_incomplete(self):
        result = self.fixture(mode='foreign', broken_status=True)
        self.assertIsNotNone(result)
        self.assertIn('incomplete owner observation', result)

    def test_reused_uid_boundary_stays_incomplete(self):
        result = self.fixture(mode='foreign', change_birth=True)
        self.assertIsNotNone(result)
        self.assertIn('incomplete owner observation', result)

    def test_same_uid_nondumpable_ancestor_named_systemd_stays_strict(self):
        result = self.fixture(mode='same')
        self.assertIsNotNone(result)
        self.assertIn('incomplete owner observation', result)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux prctl and /proc')
    def test_real_same_uid_nondumpable_ancestor_and_renamed_systemd_refuse(self):
        for name in ('ordinary', 'systemd'):
            with self.subTest(name=name):
                code = ("import ctypes,subprocess,sys; libc=ctypes.CDLL(None); "
                        "assert libc.prctl(4,0,0,0,0)==0; "
                        "assert libc.prctl(15,sys.argv[1].encode(),0,0,0)==0; "
                        "p=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()']); "
                        "print(p.pid,flush=True); p.wait()")
                with subprocess.Popen([sys.executable, '-c', code, name], cwd=self.home,
                                      env={'PATH': os.defpath}, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True) as parent:
                    try:
                        child = int(parent.stdout.readline())
                        self.assertEqual((Path('/proc') / str(parent.pid) / 'comm').read_text().strip(), name)
                        with self.assertRaises(PermissionError):
                            (Path('/proc') / str(parent.pid) / 'environ').read_bytes()
                        with mock.patch.object(self.g, '_pinned', return_value=('', '')):
                            result = self.g.check('kill ' + str(child))
                        self.assertIsNotNone(result)
                        self.assertIn('incomplete owner observation', result)
                    finally:
                        parent.stdin.close()
                        parent.wait(timeout=10)

    def test_target_is_strict_even_in_privileged_chain(self):
        result = self.fixture(target_denied=True)
        self.assertIsNotNone(result)
        self.assertIn('incomplete owner observation', result)

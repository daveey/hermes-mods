"""Linux owner-chain availability; only OS/process I/O is adapted, never deciding readers."""
import os
import subprocess
from pathlib import Path
from unittest import mock

from test_guard import Base


class LinuxBoundary(Base):
    def fixture(self, approvals='', owner='', manager_pid=4200, query_pid=4200,
                deny_environment=True, deny_cwd=False, deny_target_environment=False,
                change_birth=False, manager_target=False, query_rc=0, bad_group=False, manager_owner='',
                manager_marker=False):
        collect, lib = self.g.libs()
        proc = self.home / 'proc'
        proc.mkdir(exist_ok=True)
        uid = os.getuid()
        for pid, ppid in [(4242, manager_pid), (manager_pid, 1)]:
            root = proc / str(pid)
            root.mkdir(exist_ok=True)
            fields = ['S', str(ppid)] + ['0'] * 17 + ['12345'] + ['0'] * 5
            (root / 'stat').write_text(str(pid) + ' (systemd) ' + ' '.join(fields))
            (root / 'status').write_text('Uid:\t' + '\t'.join([str(uid)] * 4) + '\n')
            (root / 'cgroup').write_text(
                f'0::/user.slice/user-{uid}.slice/user@{uid}.service/init.scope\n')
            card = owner if pid == 4242 else manager_owner
            (root / 'environ').write_bytes(('HERMES_OWNER_CARD=' + card).encode() if card else b'')
        original_read = Path.read_bytes
        def read_bytes(path):
            if str(path).startswith('/proc/') and path.name == 'environ':
                pid = int(path.parent.name)
                if deny_target_environment and pid == 4242:
                    raise PermissionError('target environment denied')
                if deny_environment and pid == manager_pid:
                    raise PermissionError('manager environment denied')
                return original_read(proc / str(pid) / 'environ')
            return original_read(path)
        rows = f'4242 {manager_pid} 0 1024 00:10 fixturejob\n{manager_pid} 1 0 1024 00:10 systemd --user\n'
        def run(argv, **kwargs):
            if argv[:2] == ['ps', '-Ao']:
                return subprocess.CompletedProcess(argv, 0, rows, '')
            if argv[:3] == ['/usr/bin/systemctl', '--system', 'show']:
                self.assertEqual(kwargs['env'], {'PATH': os.defpath, 'LC_ALL': 'C'})
                if change_birth:
                    path = proc / str(manager_pid) / 'stat'
                    path.write_text(path.read_text().replace('12345', '12346'))
                return subprocess.CompletedProcess(argv, *command(argv), '')
            raise AssertionError('unexpected subprocess: ' + repr(argv))
        def command(argv, timeout=5):
            if Path(argv[0]).name == 'systemctl':
                return query_rc, (f'MainPID={query_pid}\nId=user@{uid}.service\nUser={uid}\n'
                                  f'ControlGroup={"/wrong" if bad_group else f"/user.slice/user-{uid}.slice/user@{uid}.service"}\n')
            raise AssertionError('unexpected command: ' + repr(argv))
        with mock.patch.object(self.g, 'PROC', proc), \
             mock.patch.object(self.g.platform, 'system', return_value='Linux'), \
             mock.patch.object(collect, 'MAC', False), \
             mock.patch.object(Path, 'read_bytes', read_bytes), \
             mock.patch.object(subprocess, 'run', run), \
             mock.patch.object(self.g, 'run_cmd', side_effect=command), \
             mock.patch.object(self.g, '_pinned', return_value=('', '')), \
             mock.patch.object(lib, '_cwd_of', side_effect=lambda pid, run: '' if deny_cwd else
                               str(self.work if pid == manager_pid and manager_marker else self.home)):
            return self.g.check('kill ' + str(manager_pid if manager_target else 4242) + ' # ' + approvals)

    def test_authenticated_manager_is_not_an_inherited_service_owner(self):
        self.assertIsNone(self.fixture())

    def test_approved_service_under_manager_is_available(self):
        self.assertIsNone(self.fixture(owner='t_aabb', approvals='owner-approved=t_aabb'))

    def test_own_card_service_under_manager_is_available(self):
        with mock.patch.dict(os.environ, {'HERMES_KANBAN_TASK': 't_aabb'}):
            self.assertIsNone(self.fixture(owner='t_aabb'))

    def test_readable_manager_owner_is_not_lost(self):
        result = self.fixture(manager_owner='t_ccdd', deny_environment=False)
        self.assertIsNotNone(result)
        self.assertIn('t_ccdd', result)

    def test_readable_manager_marker_is_not_lost(self):
        (self.work / 'OWNER.md').write_text('card: t_ccdd\n')
        result = self.fixture(deny_environment=False, manager_marker=True)
        self.assertIsNotNone(result)
        self.assertIn('t_ccdd', result)

    def test_service_owner_is_still_required(self):
        self.assertIn('t_aabb', self.fixture(owner='t_aabb'))

    def test_unreadable_target_environment_is_not_absent(self):
        result = self.fixture(deny_target_environment=True)
        self.assertIsNotNone(result)
        self.assertIn('incomplete owner observation', result)

    def test_unreadable_target_cwd_is_not_absent(self):
        result = self.fixture(deny_cwd=True)
        self.assertIsNotNone(result)
        self.assertIn('incomplete owner observation', result)

    def test_unauthenticated_manager_remains_fail_closed(self):
        for case in [dict(query_pid=4201), dict(query_rc=1), dict(bad_group=True), dict(change_birth=True)]:
            with self.subTest(case=case):
                self.assertIn('incomplete owner observation', self.fixture(**case))

    def test_manager_as_target_is_never_exempt(self):
        self.assertIn('incomplete owner observation', self.fixture(manager_target=True))

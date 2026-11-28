"""F1 plugin boundary controls; source is synthetic and all effects stay local."""
import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('custody_' + name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('kind', ['writable','foreign','symlink','file_race'])
def test_F1_waiter_directory_and_read_custody(tmp_path, monkeypatch, kind):
    waiter = module('waiter')
    parent = tmp_path / 'runner'; parent.mkdir()
    path = parent / 'hermes-sync'; path.write_text('# fixture'); path.chmod(0o600)
    monkeypatch.setattr(waiter, 'updater_path', lambda: path)
    if kind == 'writable':
        parent.chmod(0o777)
    elif kind == 'symlink':
        moved = tmp_path / 'moved'; parent.rename(moved); parent.symlink_to(moved)
    else:
        real = os.fstat
        target = (parent if kind == 'foreign' else path).stat().st_ino
        calls = 0
        def observe(fd):
            nonlocal calls
            info = real(fd)
            if info.st_ino == target:
                fields = list(info)
                if kind == 'foreign': fields[4] = os.getuid() + 1
                else:
                    calls += 1
                    if calls == 2: fields[6] = info.st_size + 1
                return os.stat_result(fields)
            return info
        monkeypatch.setattr(os, 'fstat', observe)
    with pytest.raises((OSError, ValueError)):
        waiter.attested_source()


def test_F1_supported_fixed_home_alias(tmp_path, monkeypatch):
    waiter = module('waiter')
    home = tmp_path / 'user'; home.mkdir()
    actual = home / '.private-home'
    runner = actual / 'sync-runner/hermes-sync'; runner.parent.mkdir(parents=True)
    runner.write_bytes(b'# fixture'); runner.chmod(0o600)
    (home / '.hermes').symlink_to(actual)
    monkeypatch.setenv('HOME', str(home))
    assert waiter.updater_path() == runner
    assert waiter.attested_source() == b'# fixture'


def test_F1_foreign_fixed_home_alias(tmp_path, monkeypatch):
    waiter = module('waiter')
    home = tmp_path / 'user'; home.mkdir()
    (home / '.hermes').symlink_to(home / '.private-home')
    monkeypatch.setenv('HOME', str(home))
    real = Path.lstat
    def foreign(path, *a, **kw):
        info = real(path, *a, **kw)
        if path == home / '.hermes':
            fields = list(info); fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(Path, 'lstat', foreign)
    with pytest.raises(ValueError, match='foreign'):
        waiter.updater_path()


@pytest.mark.parametrize('args, expected', [([], 'core-waiter'), (['--update'], 'core'), (['bad'], 'core-waiter')])
def test_F1_waiter_hands_opened_bytes_to_runner(tmp_path, monkeypatch, args, expected):
    waiter = module('waiter')
    path = tmp_path / 'runner'; output = tmp_path / 'receipt'
    # The source checks the injected namespace without using real updater code.
    source = (f"from pathlib import Path\nimport sys\nPath({str(output)!r}).write_text("
              "str((isinstance(_HERMES_SYNC_SOURCE, bytes), sys.argv[1:])))\n").encode()
    path.write_bytes(source); path.chmod(0o600)
    monkeypatch.setattr(waiter, 'updater_path', lambda: path)
    monkeypatch.setattr(sys, 'argv', ['waiter', *args])
    waiter.main()
    assert output.read_text() == str((True, [expected]))


def test_F1_real_managed_bootstrap_to_waiter_boundary(tmp_path, monkeypatch):
    """Real child + real waiter, deterministic managed-bootstrap/parser doubles.

    No production updater or actual bootstrap repair is executed.
    """
    import subprocess
    core = module('core')
    core_root = tmp_path / 'core'; core_root.mkdir()
    parser_root = tmp_path / 'managed-deps'; parser_root.mkdir()
    (parser_root / 'fixture_yaml.py').write_text('available = True\n')
    (core_root / 'hermes_bootstrap.py').write_text(f'import sys\nsys.path.append({str(parser_root)!r})\n')
    plugin = tmp_path / 'plugin'; plugin.mkdir()
    (plugin / 'waiter.py').write_bytes((ROOT / 'waiter.py').read_bytes())
    home = tmp_path / 'home'; home.mkdir()
    runner = home / '.hermes/sync-runner/hermes-sync'; runner.parent.mkdir(parents=True)
    output = tmp_path / 'receipt'
    runner.write_text('import sys, fixture_yaml\nfrom pathlib import Path\n'
                      + f'Path({str(output)!r}).write_text(str((sys.flags.isolated, sys.argv[1:], fixture_yaml.available, isinstance(_HERMES_SYNC_SOURCE, bytes))))\n')
    runner.chmod(0o600)
    monkeypatch.setattr(core, '__file__', str(plugin / 'core.py'))
    monkeypatch.setattr(core, 'running_checkout', lambda: core_root)
    monkeypatch.setattr(core, '_read_state', lambda *a: {})
    monkeypatch.setattr(core, '_status', lambda *a: {'state':'ready','can_restart':True,'sha':'a'*40})
    monkeypatch.setattr(core, '_write_restart_force_request', lambda *a: None)
    seen=[]
    raw = getattr(subprocess, '_custody_fixture_child_popen', subprocess.Popen)
    assert core.restart(home=home/'.hermes',popen=lambda argv,**kw:seen.append(argv))['ok']
    argv = seen[0]
    ancestors = [(p.stat().st_dev,p.stat().st_ino) for p in tmp_path.parents]
    # Child guard runs before the actual captured bootstrap; only temp-root
    # ancestor modes are adapted, exactly as the parent fixture does.
    prefix = f'''import os,sys
_real=os.fstat
def _fixture_stat(fd):
    s=_real(fd)
    if (s.st_dev,s.st_ino) in {ancestors!r}:
        v=list(s);v[0]=int(v[0])&~0o022;return os.stat_result(v)
    return s
os.fstat=_fixture_stat
def _deny(event,args):
    if event in ('sqlite3.connect','socket.connect','subprocess.Popen','os.system','os.kill','os.exec'):
        raise RuntimeError('child effect denied: '+event)
sys.addaudithook(_deny)
'''
    argv[3] = prefix + argv[3]
    proc = raw(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
               env={**os.environ,'HOME':str(home),'HERMES_HOME':str(home/'.hermes')})
    out,err=proc.communicate(timeout=20)
    assert proc.returncode==0,err.decode()
    assert output.read_text()==str((1,['core-waiter'],True,True))

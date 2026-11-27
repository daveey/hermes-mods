"""Source-review D1/D2/D3 regressions; all files and homes are disposable."""
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('source_review_guard', 'guard.py')
swap = load('source_review_swap', 'swap_db.py')


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / 'user'
    home = user / '.hermes'
    (home / 'cache').mkdir(parents=True)
    (home / 'state.db').write_bytes(b'fixture database')
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(guard, 'program_sqlite_version', lambda *a: None)
    return home


@pytest.mark.parametrize('prefix', ['~/.hermes', '$HOME/.hermes', '${HERMES_HOME}'])
@pytest.mark.parametrize('command', ['rm -f {p}/cache/../state.db', 'cp copy {p}/cache/../state.db',
                                     'echo x > {p}/cache/../state.db-wal'])
def test_d1_normalized_path(home, prefix, command):
    assert guard.check_shell(command.format(p=prefix))


def test_d1_relative_normalized_path(home):
    assert guard.check_shell('rm -f cache/../state.db', cwd=str(home))


def test_d1_local_symlink_alias(home, tmp_path):
    alias = home / 'cache' / 'alias.db'
    alias.symlink_to(home / 'state.db')
    assert guard.check_shell(f'rm -f {alias}')
    assert guard.check_shell(f'cp copy {alias}')


@pytest.mark.parametrize('command', ['rm -rf ~/.hermes', 'rm --recursive --force $HERMES_HOME',
                                     'rm -Rf ~/.hermes/inbox', 'mv ~/.hermes elsewhere',
                                     'D=~/.hermes; rm -rf "$D"',
                                     'ssh example.com "rm -rf ~/.hermes"'])
def test_d1_ancestor(home, command):
    assert guard.check_shell(command)


@pytest.mark.parametrize('command', ['rm -rf ~/.hermes/cache', 'rm -rf ~/.hermes/backups',
                                     'rm -rf ~/.hermes/cache/../cache', 'rm -rf /unrelated',
                                     'rm -f ~/.hermes', 'echo rm -rf ~/.hermes'])
def test_d1_safe_directory_controls(home, command):
    assert guard.check_shell(command) is None


def test_d1_existing_database_shaped_directory(home):
    directory = home / 'bundle.db.new'
    directory.mkdir()
    (directory / 'state.db').write_bytes(b'fixture')
    assert guard.check_shell(f'rm -rf {directory}')
    assert guard.check_shell(f'mv {directory} elsewhere')


def test_d1_home_parent_and_cwd(home):
    assert guard.check_shell('rm -rf ~')
    assert guard.check_shell('rm -rf .', cwd=str(home))


def test_d1_remote_normalization(home):
    assert guard.check_shell('ssh example.com "rm -f ~/.hermes/cache/../state.db"')


def test_d1_remote_explicit_database_shaped_directory(home):
    assert guard.check_shell('ssh example.com "rm -rf ~/.hermes/bundle.db.new/"')
    assert guard.check_shell('ssh example.com "mv ~/.hermes/copy.db.new /unrelated/copy"') is None


def test_d1_remote_paths_never_resolve_local_symlinks(home, tmp_path):
    alias = home / 'cache' / 'alias.db'
    alias.symlink_to(home / 'state.db')
    # Remote cache is not live; the local file with that spelling is irrelevant.
    assert guard.check_shell(f"ssh example.com 'rm -f {alias}'") is None


@pytest.mark.parametrize('code', [
    "from os import unlink; unlink({db!r})",
    "from os import remove; remove({db!r})",
    "from os import unlink as delete; delete({db!r})",
    "import os as x; x.remove({db!r})",
    "import os as x; x.unlink(path={db!r})",
    "from os import remove as delete; p={db!r}; delete(p)",
])
def test_d2_import_aliases(home, code):
    assert guard.check_python(code.format(db=str(home / 'state.db')))


@pytest.mark.parametrize('code', [
    "from os import unlink; unlink({db!r})",
    "import os as x; x.remove({db!r})",
])
def test_d2_scratch_control(home, code):
    assert guard.check_python(code.format(db=str(home / 'cache' / 'state.db'))) is None


def test_d3_save_does_not_follow_predictable_temp_symlink(tmp_path):
    path = tmp_path / 'state.db.recover.pid'
    victim = tmp_path / 'victim'
    victim.write_text('keep me')
    victim.chmod(0o640)
    legacy = path.with_name(path.name + '.tmp')
    legacy.symlink_to(victim)
    swap._save(path, {'pid': 123})
    assert victim.read_text() == 'keep me'
    assert stat.S_IMODE(victim.stat().st_mode) == 0o640
    assert legacy.is_symlink()
    assert json.loads(path.read_text()) == {'pid': 123}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_d3_unique_temps_and_cleanup_on_publication_failure(tmp_path, monkeypatch):
    path = tmp_path / 'receipt.json'
    temps = []
    def fail(source, dest):
        source = Path(source)
        assert source.parent == path.parent
        temps.append(source)
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        raise OSError('injected publication failure')
    monkeypatch.setattr(swap.os, 'replace', fail)
    for _ in range(2):
        with pytest.raises(OSError, match='injected'):
            swap._save(path, {'ok': True})
    assert temps[0] != temps[1]
    assert all(not p.exists() for p in temps)
    assert not path.exists()

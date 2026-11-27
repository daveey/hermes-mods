"""Source-review regressions; only fixture-owned files and fake VNC/compiler."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rs_source_review', ROOT / '__init__.py')
rs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rs
spec.loader.exec_module(rs)


def line(text, x=100, y=100):
    return dict(text=text, x=x, y=y, w=300, h=20)


@pytest.mark.parametrize('requester', ['Not Terminal Helper', 'TerminalX', 'Terminal.app', 'Term-inal', 'Terminal!'])
def test_exact_requester_identity(requester):
    result = rs.find_dialog([line(f'“{requester}” would like to access files'), line('Allow', y=200)], ['Terminal'])
    assert result['status'] == 'blocked'
    assert result['reason'] == 'app_not_allowlisted'


def test_exact_identity_positive():
    result = rs.find_dialog([line('“Terminal” would like to access files'), line('Allow', y=200)], ['Terminal'])
    assert result['status'] == 'approve'  # diagnostic candidate, never click authority


class FakeClient:
    def __init__(self):
        self.clicks = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def screenshot(self, **kw):
        return SimpleNamespace(to_image=lambda: SimpleNamespace(save=lambda path: None))
    def click(self, *xy):
        self.clicks.append(xy)


@pytest.fixture
def screen(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(rs, 'hermes_home', lambda: tmp_path)
    monkeypatch.setattr(rs, 'load_config', lambda: {'hosts': {}, 'approve': {'apps': ['Terminal']}})
    monkeypatch.setattr(rs, 'quiet_blocks', lambda addr: None)
    client = FakeClient()
    monkeypatch.setattr(rs, 'connect', lambda host: client)
    monkeypatch.setattr(rs, '_save_frame', lambda *a: tmp_path / 'screen.png')
    return client


@pytest.mark.parametrize('dry_run', [False, True])
def test_screen_text_cannot_authorize_approval(screen, monkeypatch, dry_run):
    # A webpage can draw an indistinguishable request and button anywhere.
    monkeypatch.setattr(rs, 'read_dialogs', lambda path: [line('“Terminal” would like to access files'), line('Allow', y=200)])
    result = json.loads(rs.handle_approve({'host': 'example.invalid', 'dry_run': dry_run}))
    assert screen.clicks == []
    assert result['inspection_only'] is True


@pytest.mark.parametrize('action', ['click', 'double_click', 'right_click', 'move', 'scroll'])
def test_screen_text_cannot_select_input_target(screen, monkeypatch, action):
    monkeypatch.setattr(rs, 'ocr', lambda path: [line('Allow')])
    result = json.loads(rs.handle_input({'host': 'example.invalid', 'action': action, 'target_text': 'Allow', 'capture_after': False}))
    assert screen.clicks == []
    assert result['ok'] is False
    assert result['code'] == 'untrusted_text_target'


def test_explicit_coordinates_still_work(screen):
    result = json.loads(rs.handle_input({'host': 'example.invalid', 'action': 'click', 'x': 12, 'y': 34, 'screen_pixels': True, 'capture_after': False}))
    assert result['ok'] is True
    assert screen.clicks == [(12, 34)]


def test_app_filter_is_exact_before_connection(screen):
    result = json.loads(rs.handle_approve({'host': 'example.invalid', 'app': 'Term'}))
    assert result['ok'] is False
    assert 'not in approve.apps' in result['error']


@pytest.mark.parametrize('address', ["x' ; touch SENTINEL ; '", '$(touch SENTINEL)', 'example.invalid'])
def test_setup_shell_arguments_are_literal(tmp_path, address):
    h = {'user': "u' ; touch SENTINEL ; '", 'address': address}
    command = rs.setup_command(h)
    args = shlex.split(command)
    assert args == ['security', 'add-generic-password', '-U', '-s', rs.KEYCHAIN_SERVICE, '-a', rs.keychain_account(h), '-l', f'Hermes remote-screen: {address}', '-w']
    # Real shell parsing, fake security executable, no credentials or networking.
    binary = tmp_path / 'security'
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_FILE"\n')
    binary.chmod(0o700)
    subprocess.run(['/bin/sh', '-c', command], cwd=tmp_path, env={**os.environ, 'PATH': str(tmp_path) + ':/usr/bin:/bin', 'ARGS_FILE': str(tmp_path / 'args')}, check=True)
    assert not (tmp_path / 'SENTINEL').exists()
    assert (tmp_path / 'args').read_text().splitlines() == args[1:]


@pytest.fixture
def compiler(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, 'cache_dir', lambda *args: tmp_path)
    monkeypatch.setattr(rs.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(rs.shutil, 'which', lambda name: '/untrusted/PATH/swiftc')
    legacy = tmp_path / 'remote-screen-ocr'
    legacy.write_text('untrusted binary')
    os.utime(legacy, (4102444800, 4102444800))
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        if argv[0].endswith('swiftc'):
            Path(argv[argv.index('-o') + 1]).write_bytes(b'fixture compiled OCR')
            return SimpleNamespace(returncode=0, stderr='')
        return SimpleNamespace(returncode=0, stdout='[]', stderr='')
    monkeypatch.setattr(rs.subprocess, 'run', run)
    return calls, legacy


def test_future_mtime_binary_is_not_executed(compiler):
    calls, legacy = compiler
    assert rs.ocr(Path('fixture.png')) == []
    assert calls[0][0] == '/usr/bin/swiftc'
    assert all(argv[0] != str(legacy) for argv in calls)
    assert not Path(calls[-1][0]).exists()  # private build is removed after use


def test_ocr_build_is_bound_to_reviewed_source(compiler, tmp_path, monkeypatch):
    calls, _ = compiler
    fake_plugin = tmp_path / 'plugin'
    fake_plugin.mkdir()
    (fake_plugin / 'ocr.swift').write_text('unreviewed source')
    monkeypatch.setattr(rs, '__file__', str(fake_plugin / '__init__.py'))
    with pytest.raises(RuntimeError, match='source digest'):
        rs.ocr(Path('fixture.png'))
    assert calls == []


def test_binary_digest_checked_before_execution(compiler, monkeypatch):
    calls, _ = compiler
    monkeypatch.setattr(rs, 'hashlib', hashlib, raising=False)
    original_hash = hashlib.sha256
    count = 0
    def digest(data=b''):
        nonlocal count
        if data == b'fixture compiled OCR':
            count += 1
            if count == 2:
                return original_hash(b'tampered executable')
        return original_hash(data)
    monkeypatch.setattr(rs.hashlib, 'sha256', digest, raising=False)
    with pytest.raises(RuntimeError, match='binary digest'):
        rs.ocr(Path('fixture.png'))
    assert len(calls) == 1


def test_compiler_failure_never_executes_cached_binary(compiler, monkeypatch):
    calls, _ = compiler
    def fail(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stderr='fixture build failure')
    monkeypatch.setattr(rs.subprocess, 'run', fail)
    with pytest.raises(RuntimeError):
        rs.ocr(Path('fixture.png'))
    assert len(calls) == 1
    assert calls[0][0] == '/usr/bin/swiftc'


def test_ocr_refuses_non_macos(compiler, monkeypatch):
    calls, _ = compiler
    monkeypatch.setattr(rs.platform, 'system', lambda: 'Linux')
    with pytest.raises(RuntimeError, match='macOS'):
        rs.ocr(Path('fixture.png'))
    assert calls == []


@pytest.mark.parametrize('kind', ['symlink', 'missing', 'directory'])
def test_compiler_must_produce_regular_binary(compiler, monkeypatch, kind):
    calls, legacy = compiler
    def invalid(argv, **kw):
        calls.append(argv)
        if not argv[0].endswith('swiftc'):
            return SimpleNamespace(returncode=0, stdout='[]', stderr='')
        out = Path(argv[argv.index('-o') + 1])
        if kind == 'symlink':
            out.symlink_to(legacy)
        elif kind == 'directory':
            out.mkdir()
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(rs.subprocess, 'run', invalid)
    with pytest.raises(RuntimeError, match='regular binary'):
        rs.ocr(Path('fixture.png'))
    assert len(calls) == 1
    assert not Path(calls[0][calls[0].index('-o') + 1]).parent.exists()


def test_ocr_execution_failure_cleans_private_build(compiler, monkeypatch):
    calls, _ = compiler
    original = rs.subprocess.run
    def fail_execution(argv, **kw):
        if argv[0].endswith('swiftc'):
            return original(argv, **kw)
        calls.append(argv)
        return SimpleNamespace(returncode=1, stderr='execution refused')
    monkeypatch.setattr(rs.subprocess, 'run', fail_execution)
    with pytest.raises(RuntimeError, match='OCR failed'):
        rs.ocr(Path('fixture.png'))
    assert not Path(calls[-1][0]).exists()


@pytest.mark.parametrize('dry_run', [False, True])
def test_inspection_payload_and_log_have_no_target_geometry(screen, monkeypatch, tmp_path, dry_run):
    monkeypatch.setattr(rs, 'read_dialogs', lambda path: [
        line('Web page says: “Terminal” would like to access files'), line('Allow', x=300, y=200)])
    result = json.loads(rs.handle_approve({'host': 'example.invalid', 'dry_run': dry_run}))
    assert result['status'] == 'candidate'
    assert result['inspection_only'] is True
    def no_geometry(value):
        if isinstance(value, dict):
            assert not ({'button', 'request', 'x', 'y', 'w', 'h'} & value.keys())
            for item in value.values():
                no_geometry(item)
        elif isinstance(value, list):
            for item in value:
                no_geometry(item)
    no_geometry(result)
    logs = list(tmp_path.glob('cache/remote-screen/*/approvals/*.json'))
    assert len(logs) == 1
    no_geometry(json.loads(logs[0].read_text()))
    assert screen.clicks == []
    description = rs.SCHEMAS['remote_input']['parameters']['properties']['screen_pixels']['description']
    assert 'OCR' not in description


def test_dialog_inspection_compiles_once_for_all_regions(compiler, tmp_path):
    calls, _ = compiler
    from PIL import Image
    png = tmp_path / 'screen.png'
    Image.new('RGB', (1200, 1200)).save(png)
    original = rs.subprocess.run
    def run(argv, **kw):
        result = original(argv, **kw)
        if not argv[0].endswith('swiftc'):
            result.stdout = json.dumps([line('“Terminal” would like to access files', y=100),
                                       line('“Terminal” would like to access files', y=700)])
        return result
    # compiler's monkeypatch fixture owns and restores this module-global binding.
    rs.subprocess.run = run
    rs.read_dialogs(png)
    assert sum(argv[0].endswith('swiftc') for argv in calls) == 1
    assert len(calls) == 4  # one build, full-screen OCR, two zooms
    assert len({argv[0] for argv in calls if not argv[0].endswith('swiftc')}) == 1
    assert not Path(calls[-1][0]).exists()


def test_shared_binary_rechecked_before_zoom(compiler, tmp_path, monkeypatch):
    calls, _ = compiler
    from PIL import Image
    png = tmp_path / 'screen.png'
    Image.new('RGB', (1200, 1200)).save(png)
    original = rs.subprocess.run
    def run(argv, **kw):
        result = original(argv, **kw)
        if not argv[0].endswith('swiftc'):
            result.stdout = json.dumps([line('“Terminal” would like to access files')])
            Path(argv[0]).write_bytes(b'tampered after first OCR')
        return result
    monkeypatch.setattr(rs.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='binary digest'):
        rs.read_dialogs(png)
    assert len(calls) == 2  # no zoom execution after tampering
    assert not Path(calls[-1][0]).exists()
    assert list(tmp_path.glob('*-zoom*.png')) == []


def test_no_open_default_button():
    result = rs.find_dialog([line('“Terminal” would like to access files'), line('Open', y=200)], ['Terminal'])
    assert result['reason'] == 'no_approve_button'

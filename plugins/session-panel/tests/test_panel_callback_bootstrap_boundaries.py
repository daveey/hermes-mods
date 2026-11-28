"""Closed registrar policy boundary tests; whole-source loading, never callback extraction."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest

pytest.importorskip('hermes_cli.serve_producer_callbacks')
from hermes_cli.serve_producer_protocol import ProtocolError
from test_panel_callback_bootstrap import real_registrations
from test_panel_provider_manifest import load_api, _scratch_home


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    _scratch_home(tmp_path, monkeypatch)
    real_registrations(tmp_path, monkeypatch)
    api = load_api()
    return api._callback_bootstrap_mod(), api._panel_provider_spec()['manifest']


@pytest.mark.parametrize('case', ['none', 'list', 'missing', 'extra', 'unknown', 'wrong-kind', 'both-kinds',
                                    'entry-list', 'entry-extra', 'root', 'files-list', 'files-missing',
                                    'files-extra', 'digest', 'function-path', 'function-digest'])
def test_support_refusals_before_any_registrar_import(prepared, monkeypatch, case):
    module, original = prepared
    manifest = copy.deepcopy(original)
    support = manifest['callback_bootstrap']
    entry = support['chat-closeout']
    descriptor = manifest['callback_registry']['waiting']['chat-closeout']['function']
    if case == 'none': manifest['callback_bootstrap'] = None
    elif case == 'list': manifest['callback_bootstrap'] = []
    elif case == 'missing': del support['chat-closeout']
    elif case == 'extra': support['extra'] = entry
    elif case == 'unknown':
        manifest['callback_registry']['waiting']['other'] = descriptor
        support['other'] = entry
    elif case == 'wrong-kind':
        manifest['callback_registry']['done']['chat-closeout'] = manifest['callback_registry']['waiting'].pop('chat-closeout')
    elif case == 'both-kinds': manifest['callback_registry']['done']['chat-closeout'] = descriptor
    elif case == 'entry-list': support['chat-closeout'] = ['root', 'files']
    elif case == 'entry-extra': entry['unexpected'] = True
    elif case == 'root': entry['root'] += '/foreign'
    elif case == 'files-list': entry['files'] = list(entry['files'])
    elif case == 'files-missing': del entry['files']['waiting_inputs.py']
    elif case == 'files-extra': entry['files']['owner_wake.py'] = '0' * 64
    elif case == 'digest': entry['files']['core.py'] = '0' * 64
    elif case == 'function-path': descriptor['path'] = '/foreign/' + Path(descriptor['path']).name
    elif case == 'function-digest': descriptor['sha256'] = '0' * 64
    calls = []
    monkeypatch.setattr(module.importlib, 'import_module', lambda *a: calls.append(a))
    with pytest.raises(ProtocolError):
        module.bootstrap(manifest)
    assert not calls


@pytest.mark.parametrize('suffix', ['', '.core'])
def test_preexisting_callback_namespace_refused(prepared, monkeypatch, suffix):
    module, manifest = prepared
    monkeypatch.setitem(sys.modules, '_panel_safe_chat_closeout' + suffix, types.ModuleType('foreign'))
    with pytest.raises(ProtocolError, match='namespace already'):
        module.bootstrap(manifest)


def test_empty_inventory_imports_no_registrar(prepared, monkeypatch):
    module, _ = prepared
    assert module.capture_support({'done': {}, 'waiting': {}}) == {}
    monkeypatch.setattr(module.importlib, 'import_module', lambda *a: pytest.fail('empty bootstrap imported'))
    module.bootstrap({'callback_registry': {'done': {}, 'waiting': {}}, 'callback_bootstrap': {}})


def test_verified_loader_executes_frozen_source_not_replaced_disk_or_pyc(prepared, tmp_path):
    module, _ = prepared
    path = tmp_path / 'payload.py'
    path.write_text('value = "untrusted disk"\n')
    loader = module._VerifiedLoader(path, b'value = "authenticated snapshot"\n', False)
    spec = importlib.util.spec_from_loader('_frozen_callback', loader)
    assert spec is not None
    loaded = importlib.util.module_from_spec(spec)
    loader.exec_module(loaded)
    assert loaded.value == 'authenticated snapshot'
    assert loaded.__file__ == str(path)
    assert not hasattr(loaded, '__path__'), 'a module must not acquire package import authority'


def test_closed_finder_ignores_unrelated_and_refuses_undeclared(prepared, tmp_path):
    module, _ = prepared
    finder = module._ClosedFinder({'_safe_test': (tmp_path, {'__init__.py': b'', 'core.py': b'x=1'})})
    assert finder.find_spec('ordinary') is None
    assert finder.find_spec('_safe_test_extra') is None
    for name in ('_safe_test.owner_wake', '_safe_test.deep.core'):
        with pytest.raises(ProtocolError, match='undeclared'):
            finder.find_spec(name)
    package = finder.find_spec('_safe_test')
    child = finder.find_spec('_safe_test.core')
    assert package is not None and child is not None
    assert package.submodule_search_locations is not None
    assert child.submodule_search_locations is None
    loaded = importlib.util.module_from_spec(package)
    package.loader.exec_module(loaded)
    assert loaded.__path__ == [str(tmp_path)]


def test_support_read_is_bounded_missing_and_escape_are_refused(prepared, tmp_path, monkeypatch):
    module, manifest = prepared
    with pytest.raises(ProtocolError, match='missing'):
        module._read(tmp_path / 'missing')
    oversized = tmp_path / 'large'
    oversized.write_bytes(b'x' * 6)
    monkeypatch.setattr(module, 'MAX_SOURCE', 5)
    with pytest.raises(ProtocolError, match='too large'):
        module._read(oversized)


def test_support_file_symlink_cannot_escape_authenticated_root(prepared, tmp_path, monkeypatch):
    module, manifest = prepared
    root = tmp_path / 'safe'
    root.mkdir()
    foreign = tmp_path / 'foreign.py'
    foreign.write_text('raise AssertionError("must never execute")\n')
    (root / '__init__.py').symlink_to(foreign)
    monkeypatch.setattr(module, '_root', lambda name: root)
    manifest = copy.deepcopy(manifest)
    # Keep only chat-closeout so the first checked member is the escape.
    manifest['callback_registry']['done'] = {}
    manifest['callback_bootstrap'] = {'chat-closeout': {
        'root': str(root), 'files': {name: hashlib.sha256(b'').hexdigest()
                                  for name in ('__init__.py', 'core.py', 'waiting_inputs.py', 'hook_work.py')}}}
    with pytest.raises(ProtocolError, match='escapes root'):
        module._authenticated(manifest)

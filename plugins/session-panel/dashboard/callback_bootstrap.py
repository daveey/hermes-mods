"""Closed registration-only callback bootstrap; no discovery, generic register or agent startup.

Only sibling implementations explicitly listed here are executable. All support bytes are authenticated
before the first plugin import. Private package loaders compile those exact bounded bytes, not pyc files;
late imports in that namespace cannot escape the declared closure. Core compares the actual registrations
and identity-bound companions after this factory returns, before initial publication/READY.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.util
from pathlib import Path
import sys
import types

from hermes_cli.serve_producer_protocol import ProtocolError

MAX_SOURCE = 4 * 1024 * 1024
# A closed policy, not names from a request or plugin enablement guesses.
POLICY = {
    'kanban-honest-done': ('done', 'core', 'register_with',
                         ('core.py', 'acceptance.py', 'comment_branches.py', 'consent.py', 'delivery.py',
                          'origin_gone.py', 'origin_receipts.py', 'shell_commands.py', 'thresholds.py')),
    'chat-closeout': ('waiting', '', 'register_waiting_with',
                      ('__init__.py', 'core.py', 'waiting_inputs.py', 'hook_work.py')),
}


def _root(name):
    return (Path(__file__).resolve().parents[2] / name).resolve(strict=True)


def _read(path):
    try:
        with path.open('rb') as stream:
            data = stream.read(MAX_SOURCE + 1)
    except OSError:
        raise ProtocolError('callback support missing') from None
    if len(data) > MAX_SOURCE:
        raise ProtocolError('callback support too large')
    return data


def capture_support(registry):
    """Snapshot support for real, supported registrations. Unknown registrations fail in the child, not empty."""
    result = {}
    for name, (kind, _, _, files) in POLICY.items():
        if name in registry[kind]:
            root = _root(name)
            result[name] = {'root': str(root), 'files': {
                file: hashlib.sha256(_read(root / file)).hexdigest() for file in files}}
    return result


def _authenticated(manifest):
    registry = manifest['callback_registry']
    support = manifest.get('callback_bootstrap')
    wanted = set(registry['done']) | set(registry['waiting'])
    if type(support) is not dict or set(support) != wanted or not wanted <= POLICY.keys():
        raise ProtocolError('unsupported configured callback bootstrap')
    authenticated = {}
    for name in sorted(wanted):
        kind, _, _, files = POLICY[name]
        # wanted is the union of these two registries: exclusion from the other
        # kind already implies membership in this one. Keep one authority check.
        if name in registry['waiting' if kind == 'done' else 'done']:
            raise ProtocolError('callback registration kind')
        entry = support[name]
        root = _root(name)
        if type(entry) is not dict or set(entry) != {'root', 'files'} or entry['root'] != str(root):
            raise ProtocolError('callback registration root')
        digests = entry['files']
        if type(digests) is not dict or set(digests) != set(files):
            raise ProtocolError('callback support closure')
        data = {}
        for file in files:
            path = root / file
            if path.resolve(strict=True).parent != root:
                raise ProtocolError('callback support escapes root')
            source = _read(path)
            if hashlib.sha256(source).hexdigest() != digests[file]:
                raise ProtocolError('callback support digest mismatch')
            data[file] = source
        # Every plugin-owned function/companion must come from this very closure, with the same digest.
        for descriptor in registry[kind][name].values():
            if descriptor is None:
                continue
            path = Path(descriptor['path'])
            if path.parent != root or digests.get(path.name) != descriptor['sha256']:
                raise ProtocolError('callback function outside authenticated closure')
        authenticated[name] = (root, data)
    return authenticated


class _VerifiedLoader(importlib.abc.Loader):
    def __init__(self, path, source, package):
        self.path, self.source, self.package = path, source, package

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = str(self.path)
        if self.package:
            module.__path__ = [str(self.path.parent)]
        # Whole authenticated source module, never AST extraction or a callback text slice.
        code = compile(self.source, str(self.path), 'exec', dont_inherit=True)
        exec(code, module.__dict__)


class _ClosedFinder(importlib.abc.MetaPathFinder):
    def __init__(self, packages):
        self.packages = packages

    def find_spec(self, fullname, path=None, target=None):
        for prefix, (root, files) in self.packages.items():
            if fullname == prefix or fullname.startswith(prefix + '.'):
                suffix = fullname[len(prefix):].lstrip('.')
                file = '__init__.py' if not suffix else suffix + '.py'
                if file not in files:
                    raise ProtocolError('undeclared callback module import')
                loader = _VerifiedLoader(root / file, files[file], not suffix)
                return importlib.util.spec_from_loader(fullname, loader, is_package=not suffix)
        return None


def bootstrap(manifest):
    authenticated = _authenticated(manifest)  # authenticate ALL before ANY registrar import
    if not authenticated:
        return
    from hermes_cli import kanban_session_activity as activity
    from hermes_cli.serve_producer_callbacks import verify
    packages = {}
    for name, (root, files) in authenticated.items():
        prefix = '_panel_safe_' + name.replace('-', '_')
        if any(loaded == prefix or loaded.startswith(prefix + '.') for loaded in sys.modules):
            raise ProtocolError('callback namespace already loaded')
        packages[prefix] = (root, files)
    finder = _ClosedFinder(packages)
    sys.meta_path.insert(0, finder)
    # Kept for the child's lifetime: any deferred sibling import uses the authenticated frozen bytes.
    for name, (root, files) in authenticated.items():
        _, module_name, registrar, _ = POLICY[name]
        prefix = '_panel_safe_' + name.replace('-', '_')
        if module_name:
            package = types.ModuleType(prefix)
            package.__path__ = [str(root)]
            sys.modules[prefix] = package  # synthetic namespace; NEVER the honest-done generic __init__
        for file in files:
            suffix = '' if file == '__init__.py' else '.' + Path(file).stem
            importlib.import_module(prefix + suffix)
        module = sys.modules[prefix + ('.' + module_name if module_name else '')]
        getattr(module, registrar)(activity)
    verify(manifest['callback_registry'])

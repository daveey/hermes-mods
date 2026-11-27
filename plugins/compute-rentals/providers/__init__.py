"""Read-only provider collector factory.

Call build_collectors ONCE per inventory service and retain its callables so
provider caches survive UI refreshes. No provider configuration is changed and
no cloud/cluster calls happen during construction.

Extra (e.g. private) providers: only the files named, with their SHA-256, in the
configured `extra_providers` mapping (inside `extra_providers_dir`; names
starting with `_` or `test_` are refused) are loaded. A file is read once
without following symlinks, its digest must match, and that same buffer is
executed. Unpinned files never run. The module becomes a submodule of this
package, so it can use relative imports such as
`from .common import result` or `from .slurm import ssh_read`. A module exposes
`PROVIDER = '<id>'`, `build_collector()` returning a zero-argument collect
callable, and optionally `LABEL = '<display name>'`. A module that fails to load
or build is logged and reported as provider status `error`; it never breaks the
built-in providers.
"""
import hashlib
import logging
import os
from pathlib import Path
import re
import stat
import sys
import types

from .config import load_config
from .vast import VastCollector
from .sfcompute import MarketplaceCollector
from .ec2 import EC2Collector
from .slurm import SlurmCollector
from .common import result
from . import runpod, modal, primeintellect, lambdacloud

log = logging.getLogger(__name__)

LABELS = {
    'vast': 'Vast',
    'sfcompute': 'SF Compute',
    'ec2': 'EC2',
    'runpod': 'RunPod',
    'lambda': 'Lambda',
    'modal': 'Modal',
    'primeintellect': 'Prime Intellect',
    'slurm': 'Slurm',
}

_PROVIDER_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
MAX_EXTRA_BYTES = 1 << 20


def _read_pinned(path, digest):
    """Return the bytes of one regular, non-symlink file whose SHA-256 is ``digest``."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    fd = os.open(path, flags)  # a symlink raises here, so a pin cannot be redirected
    with os.fdopen(fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError('not a regular file')
        data = handle.read(MAX_EXTRA_BYTES + 1)
    if len(data) > MAX_EXTRA_BYTES:
        raise ValueError('extra provider too large')
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('digest mismatch')
    return data


def load_extra_module(path, sha256):
    """Import one pinned extra provider file as a submodule of this package.

    The bytes are read once, checked against the pin and compiled from that same
    buffer, so a file swapped after the check cannot run.
    """
    path = Path(path)
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError('a lowercase SHA-256 pin is required')
    data = _read_pinned(path, sha256)
    name = '%s.ext_%s' % (__name__, re.sub(r'\W', '_', path.stem))
    sys.modules.pop(name, None)  # hot reload picks up edits
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = __name__
    sys.modules[name] = module
    try:
        exec(compile(data, str(path), 'exec'), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _failed(provider, message):
    def collect():
        return result(provider, [], 'Extra provider module', [message])
    return collect


def load_extra_providers(directory, reserved=(), pins=None):
    """Return ({provider: collect}, {provider: label}) for the pinned files of one extras directory.

    Only files named in ``pins`` ({filename: sha256}) whose bytes match their digest are executed. Nothing
    else in the directory runs; unpinned ``*.py`` files are logged and skipped.
    """
    collectors, labels = {}, {}
    directory = Path(directory).expanduser()
    pins = dict(pins or {})
    if not directory.is_dir():
        return collectors, labels
    for path in sorted(directory.glob('*.py')):
        if path.name not in pins and not path.name.startswith(('_', 'test_')):
            log.warning('compute-rentals: %s is not pinned in extra_providers; not loaded', path)
    for filename in sorted(pins):
        path = directory / filename
        provider = None
        try:
            if os.path.basename(filename) != filename or not filename.endswith('.py') \
                    or filename.startswith(('_', 'test_')):
                raise ValueError('invalid extra provider file name')
            module = load_extra_module(path, pins[filename])
            provider = getattr(module, 'PROVIDER', None)
            if not isinstance(provider, str) or not _PROVIDER_ID.fullmatch(provider):
                raise ValueError('PROVIDER must be a lowercase identifier')
            if provider in reserved or provider in collectors:
                raise ValueError('duplicate provider id ' + provider)
            collect = module.build_collector()
            if not callable(collect):
                raise TypeError('build_collector() must return a callable')
            collectors[provider] = collect
            label = getattr(module, 'LABEL', None)
            if isinstance(label, str) and label:
                labels[provider] = label
        except Exception:
            log.exception('compute-rentals: extra provider %s failed to load', path)
            usable = isinstance(provider, str) and _PROVIDER_ID.fullmatch(provider) and provider not in reserved
            name = provider if usable else Path(filename).stem
            if name not in reserved and _PROVIDER_ID.fullmatch(name):
                collectors.setdefault(name, _failed(name, 'Extra provider module %s failed to load' % filename))
    return collectors, labels


def build_collectors(config=None):
    cfg = config if config is not None else load_config()
    collectors = {
        'vast': VastCollector().collect,
        'sfcompute': MarketplaceCollector().collect,
        'ec2': EC2Collector(profiles=cfg['ec2']['profiles'], cache_seconds=cfg['ec2']['cache_seconds']).collect,
        'runpod': runpod.build_collector(cfg),
        'lambda': lambdacloud.build_collector(cfg),
        'modal': modal.build_collector(cfg),
        'primeintellect': primeintellect.build_collector(cfg),
        'slurm': SlurmCollector(config=cfg['slurm']).collect,
    }
    extra, labels = load_extra_providers(cfg['extra_providers_dir'], reserved=set(collectors),
                                         pins=cfg.get('extra_providers'))
    collectors.update(extra)
    LABELS.update(labels)
    return collectors

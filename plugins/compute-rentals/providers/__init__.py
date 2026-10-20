"""Read-only provider collector factory.

Call build_collectors ONCE per inventory service and retain its callables so
provider caches survive UI refreshes. No provider configuration is changed and
no cloud/cluster calls happen during construction.

Extra (e.g. private) providers: every `*.py` in the configured
`extra_providers_dir` (names starting with `_` or `test_` are skipped) is loaded
as a submodule of this package, so it can use relative imports such as
`from .common import result` or `from .slurm import ssh_read`. A module exposes
`PROVIDER = '<id>'`, `build_collector()` returning a zero-argument collect
callable, and optionally `LABEL = '<display name>'`. A module that fails to load
or build is logged and reported as provider status `error`; it never breaks the
built-in providers.
"""
import importlib.util
import logging
from pathlib import Path
import re
import sys

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


def load_extra_module(path):
    """Import one extra provider file as a submodule of this package."""
    path = Path(path).resolve()
    name = '%s.ext_%s' % (__name__, re.sub(r'\W', '_', path.stem))
    sys.modules.pop(name, None)  # hot reload picks up edits
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _failed(provider, message):
    def collect():
        return result(provider, [], 'Extra provider module', [message])
    return collect


def load_extra_providers(directory, reserved=()):
    """Return ({provider: collect}, {provider: label}) for one extras directory."""
    collectors, labels = {}, {}
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        return collectors, labels
    for path in sorted(directory.glob('*.py')):
        if path.name.startswith(('_', 'test_')):
            continue
        provider = None
        try:
            module = load_extra_module(path)
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
            name = provider if usable else path.stem
            if name not in reserved:
                collectors.setdefault(name, _failed(name, 'Extra provider module %s failed to load' % path.name))
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
    extra, labels = load_extra_providers(cfg['extra_providers_dir'], reserved=set(collectors))
    collectors.update(extra)
    LABELS.update(labels)
    return collectors

"""Optional plugin configuration (`<HERMES_HOME>/compute-rentals.yaml`).

The file is optional: a missing file yields the defaults below. Its path can be
overridden with the COMPUTE_RENTALS_CONFIG environment variable. Unknown
top-level keys are kept, so extra provider modules can read their own section.
PyYAML is imported lazily (the broker worker runtime does not need it); JSON is
accepted as a fallback because it is a subset of YAML.
"""
import copy
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

ENV_PATH = 'COMPUTE_RENTALS_CONFIG'
DEFAULT_WORKER_PYTHON = Path(__file__).resolve().parents[1] / '.extra-runtime' / 'bin' / 'python'


def hermes_home():
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()).expanduser()
    except Exception:
        return Path(os.environ.get('HERMES_HOME') or '~/.hermes').expanduser()


def config_path():
    override = os.environ.get(ENV_PATH)
    return Path(override).expanduser() if override else hermes_home() / 'compute-rentals.yaml'


def defaults():
    return {
        'extra_providers_dir': str(hermes_home() / 'compute-rentals' / 'providers'),
        'ec2': {'profiles': [os.environ.get('AWS_PROFILE') or 'default'], 'cache_seconds': 300},
        'slurm': {'hosts': [], 'user': None, 'control_host': None},
        'broker': {'command': [], 'python': None},
    }


def _yaml_loader():
    """Return a safe YAML load function, or None.

    Hermes itself ships ruamel.yaml (not PyYAML), so try both; PyYAML first
    because extra runtimes usually have it.
    """
    try:
        import yaml
        return yaml.safe_load
    except ImportError:
        pass
    try:
        from ruamel.yaml import YAML
    except ImportError:
        return None
    return YAML(typ='safe', pure=True).load


def _parse(text, loader=None):
    loader = loader or _yaml_loader()
    if loader is not None:
        return (loader(text) if text and text.strip() else None) or {}
    try:
        return json.loads(text or '{}')
    except json.JSONDecodeError as exc:
        raise ValueError('compute-rentals config is not JSON and no YAML parser '
                         '(PyYAML or ruamel.yaml) is installed: %s' % exc) from exc


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif value is not None:
            out[key] = value
    return out


def _string_list(value, field):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(field + ' must be a list of nonempty strings')
    return list(value)


def validate(cfg):
    cfg['extra_providers_dir'] = str(Path(str(cfg['extra_providers_dir'])).expanduser())
    ec2 = cfg['ec2']
    ec2['profiles'] = _string_list(ec2.get('profiles'), 'ec2.profiles')
    if not isinstance(ec2.get('cache_seconds'), (int, float)) or ec2['cache_seconds'] < 0:
        raise ValueError('ec2.cache_seconds must be a non-negative number')
    slurm = cfg['slurm']
    hosts = slurm.get('hosts') or []
    if isinstance(hosts, str):
        from .slurm import expand_hosts  # Slurm hostlist syntax, e.g. "node[0-4]"
        hosts = expand_hosts(hosts)
    slurm['hosts'] = _string_list(hosts, 'slurm.hosts') if hosts else []
    for field in ('user', 'control_host'):
        if slurm.get(field) is not None and not isinstance(slurm[field], str):
            raise ValueError('slurm.%s must be a string' % field)
    if slurm.get('control_host') and slurm['control_host'] not in slurm['hosts']:
        raise ValueError('slurm.control_host must be one of slurm.hosts')
    broker = cfg['broker']
    broker['command'] = _string_list(broker['command'], 'broker.command') if broker.get('command') else []
    if broker.get('python') is not None and not isinstance(broker['python'], str):
        raise ValueError('broker.python must be a string')
    return cfg


def load_config(path=None):
    """Return the merged, validated config. Raises ValueError on an invalid file."""
    path = Path(path).expanduser() if path else config_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return validate(defaults())
    data = _parse(text)
    if not isinstance(data, dict):
        raise ValueError('%s must contain a mapping' % path)
    return validate(_merge(defaults(), data))


def worker_python(cfg):
    configured = (cfg.get('broker') or {}).get('python')
    if configured:
        return str(Path(configured).expanduser())
    if DEFAULT_WORKER_PYTHON.exists():
        return str(DEFAULT_WORKER_PYTHON)
    import sys
    return sys.executable

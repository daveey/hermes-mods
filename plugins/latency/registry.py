"""Validated shipped definitions plus profile-local overrides (no remote definitions)."""
import hashlib
import json
import math
from pathlib import Path
import re


def load_yaml(path):
    try:
        from utils import load_yaml_file_readonly
    except ImportError:
        try:
            from ruamel.yaml import YAML
            return YAML(typ='safe').load(Path(path).read_text())
        except ImportError:
            import yaml
            return yaml.safe_load(Path(path).read_text())
    return load_yaml_file_readonly(Path(path))


def registry(root):
    entries = [*load_yaml(Path(__file__).with_name('metrics.yaml'))['metrics']]
    local = Path(root) / 'latency' / 'metrics.local.yaml'
    if local.exists():
        entries += (load_yaml(local) or {}).get('metrics', [])
    out = {}
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError('metric must be an object')
        required = {'id', 'label', 'group', 'kind', 'source', 'start', 'end', 'unit', 'target_p90'}
        if not required <= item.keys():
            raise ValueError('metric definition is incomplete')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', item['id']):
            raise ValueError('invalid metric id')
        if item['kind'] not in ('span', 'derived') or item['unit'] != 'ms':
            raise ValueError('invalid metric kind/unit')
        target = item['target_p90']
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or target <= 0:
            raise ValueError('target_p90 must be positive milliseconds')
        for key in ('label', 'group', 'source', 'start', 'end'):
            if not isinstance(item[key], str) or not item[key] or len(item[key]) > 200:
                raise ValueError('invalid metric ' + key)
        out[item['id']] = dict(item)
    return list(out.values())


def fingerprint(definitions):
    return hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest()

"""Strict declarative YAML and cooperating-writer, revision-fenced replacement.

normalize(mapping)/loads(text) return detached normalized Config dictionaries.
ConfigFile.load()/update(... expected_revision, expected_owner_epoch) are the disk
boundary. Initial creation uses expected_revision=0. Updates increment the stored
revision, never trust the submitted revision. Owner changes require a separately
fenced migration, not this API. All writers must use the adjacent advisory lock.

Binding verification_observation_id fields are UNTRUSTED REFERENCES, not proof.
binding_for returns only an explicit declaration, never a verified mapping. A
collector must independently authenticate evidence before projecting a host link.
Observed availability/permission/capacity claims are refused at enrollment.
"""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile

import yaml

from .models import ModelError, validate


class ConfigError(ValueError):
    code = 'configuration_error'


class _StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str or key in result:
            raise ConfigError('invalid_yaml')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
# Keep ISO timestamps as text for JSON Schema's FormatChecker.
_StrictLoader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in resolvers if tag != 'tag:yaml.org,2002:timestamp']
    for key, resolvers in _StrictLoader.yaml_implicit_resolvers.items()
}
_ARRAYS = ('scopes', 'hosts', 'bindings', 'allowed_workflows', 'providers', 'bb_bindings', 'execution_targets')
_DEFAULTS = {
    'features': dict(observe=True, repair_sessions=False, management_operations=False, dispatch=False, workspace_jobs=False),
    'retention': dict(observations_days=30, retired_days=30, audit_days=90),
    'thresholds': dict(inventory_stale_seconds=120, host_stale_seconds=180, disk_warn_gib=20, disk_fail_gib=5),
}


def _require(condition):
    if not condition:
        raise ConfigError('invalid_configuration')


def _unique(rows, key):
    values = [row[key] for row in rows]
    _require(len(values) == len(set(values)))


def _semantics(c):
    for collection in _ARRAYS:
        _unique(c[collection], 'runner_id' if collection == 'bindings' else 'id')
    _unique(c['hosts'], 'resource_id')
    opaque = [x['id'] for key in ('scopes', 'allowed_workflows', 'providers', 'bb_bindings', 'execution_targets') for x in c[key]]
    opaque += [x['resource_id'] for x in c['hosts']]
    _require(len(opaque) == len(set(opaque)))
    scopes = {x['id']: x for x in c['scopes']}
    hosts = {x['id']: x for x in c['hosts']}
    providers = {x['id']: x for x in c['providers']}
    bindings = {x['runner_id']: x for x in c['bindings']}
    bb = {x['id']: x for x in c['bb_bindings']}
    owner = dict(gateway_id=c['owner_gateway_id'], profile=c['owner_profile'], owner_epoch=c['owner_epoch'], config_revision=c['config_revision'])
    _require(c['thresholds']['disk_fail_gib'] <= c['thresholds']['disk_warn_gib'])
    scope_keys = [(x['provider'], x['endpoint'], x['kind'], x['name']) for x in c['scopes']]
    _require(len(scope_keys) == len(set(scope_keys)))
    for host in c['hosts']:
        _require(host['management'] == 'unknown' and not host['observation_ids'])
        capacity = host['capacity']
        _require(capacity['observed_active_jobs'] is None and not capacity['enforced'])
    for scope in c['scopes']:
        _unique(scope['permissions'], 'action')
        for permission in scope['permissions']:
            _require(permission['state'] in ('unknown', 'not_checked') and permission['observation_id'] is None)
    for provider in c['providers']:
        _require(provider['status'] == 'unknown' and not provider['observation_ids'])
    services = [(b['host_id'], b['service_ref']) for b in c['bindings']]
    _require(len(services) == len(set(services)))
    nodes = [(b['hub_ref'], b['node_ref']) for b in c['bb_bindings']]
    _require(len(nodes) == len(set(nodes)))
    for binding in c['bindings']:
        host = hosts.get(binding['host_id'])
        _require(host is not None and binding['service_ref'] in host['service_refs'])
    for binding in c['bb_bindings']:
        _require(binding['host_id'] in hosts and binding['provider_id'] in providers)
        _require(providers[binding['provider_id']]['kind'] in ('bb_pod', 'bb_workspace'))
        _require(binding['access'] == 'unknown' and binding['dependency_state'] == 'unknown')
    for workflow in c['allowed_workflows']:
        _require(workflow['scope_id'] in scopes)
        _unique(workflow['input_definitions'], 'input_id')
        for inp in workflow['input_definitions']:
            types = {'string': (str,), 'boolean': (bool,), 'number': (int, float)}
            _require(all(type(v) in types[inp['value_type']] for v in inp['allowed_values']))
    for target in c['execution_targets']:
        _require(target['owner'] == owner and target['provider_id'] in providers)
        _require(target['host_id'] is None or target['host_id'] in hosts)
        _require(target['availability'] == 'unknown' and not target['observation_ids'])
        provider = providers[target['provider_id']]
        _require(set(target['capability_ids']) <= set(provider['capability_ids']))
        if target['mode'] == 'ci':
            _require(provider['kind'] == 'github_actions' and target['runner_id'] in bindings)
            _require(target['host_id'] is None or target['host_id'] == bindings[target['runner_id']]['host_id'])
        else:
            binding = bb.get(target['bb_binding_id'])
            _require(binding is not None)
            _require(binding['provider_id'] == provider['id'] and binding['host_id'] == target['host_id'])
            _require(provider['kind'] == ('bb_workspace' if target['mode'] == 'workspace' else 'bb_pod'))


def normalize(mapping):
    """Insert safe defaults, then structural and semantic validation; no I/O."""
    try:
        _require(type(mapping) is dict)
        c = deepcopy(mapping)
        c.setdefault('owner_epoch', 1)
        c.setdefault('config_revision', 1)
        for name in _ARRAYS:
            c.setdefault(name, [])
        for name, defaults in _DEFAULTS.items():
            c.setdefault(name, {})
            _require(type(c[name]) is dict)
            for key, value in defaults.items():
                c[name].setdefault(key, value)
        for workflow in c['allowed_workflows']:
            workflow.setdefault('dispatch_enabled', False)
        c = validate('Config', c)
        _semantics(c)
        return c
    except (ModelError, TypeError, KeyError, AttributeError, RecursionError):
        pass
    raise ConfigError('invalid_configuration') from None


def loads(text):
    """Parse bounded safe YAML; duplicate/merge keys and invalid documents fail."""
    try:
        _require(type(text) is str and len(text.encode('utf-8')) <= 2 * 1024 * 1024)
        return normalize(yaml.load(text, Loader=_StrictLoader))
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        pass
    raise ConfigError('invalid_configuration') from None


def binding_for(config, runner_id):
    """Return a detached UNVERIFIED declaration; never infer a mapping by name."""
    return next((deepcopy(b) for b in config['bindings'] if b['runner_id'] == runner_id), None)


class ConfigFile:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            return loads(self.path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError):
            pass
        raise ConfigError('configuration_unavailable') from None

    @contextmanager
    def _lock(self):
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def update(self, mapping, *, expected_revision, expected_owner_epoch):
        """CAS full config, fsync file then atomic replace and directory fsync."""
        _require(type(expected_revision) is int and expected_revision >= 0)
        _require(type(expected_owner_epoch) is int and expected_owner_epoch >= 1)
        with self._lock():
            current = self.load() if self.path.exists() else None
            revision = current['config_revision'] if current else 0
            if revision != expected_revision:
                raise ConfigError('revision_conflict')
            candidate = deepcopy(mapping)
            _require(type(candidate) is dict)
            candidate = normalize(candidate)
            candidate['config_revision'] = revision + 1
            for target in candidate['execution_targets']:
                target['owner']['config_revision'] = revision + 1
            if candidate['owner_epoch'] != expected_owner_epoch:
                raise ConfigError('owner_conflict')
            if current and any(candidate[k] != current[k] for k in ('owner_gateway_id', 'owner_profile', 'owner_epoch')):
                raise ConfigError('owner_conflict')
            fd, name = tempfile.mkstemp(prefix='.runners-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    yaml.safe_dump(candidate, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return candidate

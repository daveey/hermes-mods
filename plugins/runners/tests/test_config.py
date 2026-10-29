"""Private configuration boundary tests, entirely fictional."""
import copy
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

FIXTURES = json.loads((Path(__file__).parents[1] / 'contracts/fixtures.json').read_text())
FIXTURES['Host']['management'] = 'unknown'
BASE = dict(schema_version=1, owner_gateway_id='00000000-0000-4000-8000-000000000001', owner_profile='example')


def test_defaults_and_no_mutation():
    from plugins.runners.config import normalize
    original = copy.deepcopy(BASE)
    result = normalize(BASE)
    assert BASE == original
    assert result['features'] == dict(observe=True, repair_sessions=False, management_operations=False, dispatch=False, workspace_jobs=False)
    assert result['retention'] == dict(observations_days=30, retired_days=30, audit_days=90)
    assert result['thresholds']['inventory_stale_seconds'] == 120
    assert result['thresholds']['host_stale_seconds'] == 180
    assert result['hosts'] == []
    assert result['config_revision'] == result['owner_epoch'] == 1


@pytest.mark.parametrize('text', ['a: SECRET\na: other', 'features: {observe: true, observe: false}', 'password: SECRET', '[]', '!!python/object:SECRET {}', 'a: &x [*x]', 'schema_version: true'])
def test_yaml_rejects_without_leaking(text):
    from plugins.runners.config import ConfigError, loads
    with pytest.raises(ConfigError) as error:
        loads(text)
    assert 'SECRET' not in str(error.value)


@pytest.mark.parametrize('mutation', ['duplicate', 'foreign_host', 'foreign_scope', 'service', 'threshold', 'observed', 'provider', 'target_owner', 'workflow_input'])
def test_semantic_rejections(mutation):
    from plugins.runners.config import ConfigError, normalize
    data = copy.deepcopy(BASE)
    host = copy.deepcopy(FIXTURES['Host'])
    data['hosts'] = [host]
    if mutation == 'duplicate':
        data['hosts'].append(dict(host, display_name='different'))
    elif mutation == 'foreign_host':
        data['bindings'] = [dict(FIXTURES['RunnerBinding'], host_id='00000000-0000-4000-8000-000000000002')]
    elif mutation == 'foreign_scope':
        data['allowed_workflows'] = [FIXTURES['Workflow']]
    elif mutation == 'service':
        data['bindings'] = [dict(FIXTURES['RunnerBinding'], service_ref='service_0000000000000002')]
    elif mutation == 'threshold':
        data['thresholds'] = dict(disk_fail_gib=50, disk_warn_gib=20)
    elif mutation == 'observed':
        host['management'] = 'reachable'
        host['observation_ids'] = ['obs_0000000000000001']
    elif mutation == 'provider':
        data['bb_bindings'] = [FIXTURES['BBBinding']]
    elif mutation == 'target_owner':
        data['providers'] = [FIXTURES['Provider']]
        data['bindings'] = [FIXTURES['RunnerBinding']]
        data['execution_targets'] = [FIXTURES['ExecutionTarget']]
    elif mutation == 'workflow_input':
        data['scopes'] = [FIXTURES['Scope']]
        inp = dict(FIXTURES['WorkflowInput'], value_type='boolean', allowed_values=['bad'])
        data['allowed_workflows'] = [dict(FIXTURES['Workflow'], input_definitions=[inp])]
    with pytest.raises(ConfigError):
        normalize(data)


def test_explicit_binding_is_declaration_not_verification():
    from plugins.runners.config import normalize, binding_for
    data = dict(BASE, hosts=[FIXTURES['Host']], bindings=[FIXTURES['RunnerBinding']])
    config = normalize(data)
    declaration = binding_for(config, FIXTURES['RunnerBinding']['runner_id'])
    assert declaration == FIXTURES['RunnerBinding']
    declaration['host_id'] = 'changed'
    assert config['bindings'][0]['host_id'] != 'changed'
    assert binding_for(config, 'runner_0000000000000002') is None
    assert config['hosts'][0]['management'] == 'unknown'
    assert config['hosts'][0]['observation_ids'] == []


def test_atomic_revision_and_owner_guard(tmp_path):
    from plugins.runners.config import ConfigError, ConfigFile
    path = tmp_path / 'runners.yaml'
    file = ConfigFile(path)
    first = file.update(BASE, expected_revision=0, expected_owner_epoch=1)
    assert first['config_revision'] == 1
    assert file.load() == first
    before = path.read_bytes()
    for revision, epoch, payload in [(0, 1, first), (1, 2, first), (1, 1, dict(first, secret='SECRET'))]:
        with pytest.raises(ConfigError):
            file.update(payload, expected_revision=revision, expected_owner_epoch=epoch)
        assert path.read_bytes() == before
    second = file.update(first, expected_revision=1, expected_owner_epoch=1)
    assert second['config_revision'] == 2
    assert file.load() == second
    assert path.stat().st_mode & 0o777 == 0o600


def test_competing_updates_only_one_wins(tmp_path):
    from plugins.runners.config import ConfigError, ConfigFile
    path = tmp_path / 'runners.yaml'
    ConfigFile(path).update(BASE, expected_revision=0, expected_owner_epoch=1)
    def write(_):
        try:
            return ConfigFile(path).update(BASE, expected_revision=1, expected_owner_epoch=1)['config_revision']
        except ConfigError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(map(str, executor.map(write, range(2)))) == ['2', 'conflict']


def test_full_declarative_graph_and_revision_updates(tmp_path):
    from plugins.runners.config import ConfigFile, normalize
    data = copy.deepcopy(FIXTURES['Config'])
    data['hosts'][0]['management'] = 'unknown'
    for provider in data['providers']:
        provider['status'] = 'unknown'
    for binding in data['bb_bindings']:
        binding['access'] = binding['dependency_state'] = 'unknown'
    for target in data['execution_targets']:
        target['availability'] = 'unknown'
    normalized = normalize(data)
    file = ConfigFile(tmp_path / 'runners.yaml')
    first = file.update(normalized, expected_revision=0, expected_owner_epoch=1)
    second = file.update(first, expected_revision=1, expected_owner_epoch=1)
    assert second['execution_targets'][0]['owner']['config_revision'] == 2
    assert file.load() == second


@pytest.mark.parametrize('change', ['service_reuse', 'bb_node_reuse', 'target_capability'])
def test_graph_collisions_and_provider_capabilities(change):
    from plugins.runners.config import ConfigError, normalize
    data = copy.deepcopy(FIXTURES['Config'])
    data['hosts'][0]['management'] = 'unknown'
    for provider in data['providers']:
        provider['status'] = 'unknown'
    for binding in data['bb_bindings']:
        binding['access'] = binding['dependency_state'] = 'unknown'
    for target in data['execution_targets']:
        target['availability'] = 'unknown'
    if change == 'service_reuse':
        data['bindings'].append(dict(data['bindings'][0], runner_id='runner_0000000000000002'))
    elif change == 'bb_node_reuse':
        data['bb_bindings'].append(dict(data['bb_bindings'][0], id='binding_0000000000000002'))
    else:
        data['execution_targets'][0]['capability_ids'] = ['capability_0000000000000002']
    with pytest.raises(ConfigError):
        normalize(data)


def test_bad_disk_config_is_not_empty_inventory(tmp_path):
    from plugins.runners.config import ConfigError, ConfigFile
    path = tmp_path / 'runners.yaml'; path.write_text('hosts: SECRET')
    with pytest.raises(ConfigError):
        ConfigFile(path).load()


def _pure_strict_loader():
    """The same strict constructor/resolver tables on the pure-Python parser."""
    from plugins.runners.config import _StrictLoader
    class Pure(yaml.SafeLoader):
        pass
    Pure.yaml_constructors = dict(_StrictLoader.yaml_constructors)
    Pure.yaml_implicit_resolvers = dict(_StrictLoader.yaml_implicit_resolvers)
    return Pure


def test_strict_loader_uses_libyaml_when_available():
    from plugins.runners.config import _StrictLoader
    if not yaml.__with_libyaml__:
        pytest.skip('libyaml not built into this PyYAML')
    assert issubclass(_StrictLoader, yaml.CSafeLoader)


_EQUIVALENCE = ['a: SECRET\na: other', 'features: {observe: true, observe: false}', '[]', '!!python/object:SECRET {}',
                'a: &x [*x]', 'schema_version: true', '1: x', 'when: 2026-10-29T22:57:22Z\nday: 2026-10-29',
                'base: &b {x: 1}\nother: {<<: *b, y: 2}', 'n: [1, 2.5, null, ~, yes, "s"]', '[: invalid']


@pytest.mark.parametrize('text', _EQUIVALENCE)
def test_libyaml_and_pure_strict_loaders_agree(text):
    from plugins.runners.config import _StrictLoader
    def outcome(loader):
        try:
            return 'ok', repr(yaml.load(text, Loader=loader))  # repr: 'a: &x [*x]' is self-referential
        except Exception as exc:  # the exception class is the contract, not the message
            return 'error', 'yaml' if isinstance(exc, yaml.YAMLError) else type(exc).__name__
    assert outcome(_StrictLoader) == outcome(_pure_strict_loader())


def test_libyaml_and_pure_agree_on_a_full_graph():
    from plugins.runners.config import _StrictLoader, normalize
    cfg = normalize(dict(BASE, scopes=[dict(FIXTURES['Scope'], id=f'scope_{i + 1:016x}', name=f'fictional/p-{i}')
                                       for i in range(50)]))
    text = yaml.safe_dump(cfg)
    assert yaml.load(text, Loader=_StrictLoader) == yaml.load(text, Loader=_pure_strict_loader()) == cfg

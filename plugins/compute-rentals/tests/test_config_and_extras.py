import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers import build_collectors, load_extra_providers  # noqa: E402
from providers.config import ENV_PATH, load_config  # noqa: E402

BUILTINS = {'vast', 'sfcompute', 'ec2', 'runpod', 'modal', 'primeintellect', 'slurm'}

GOOD = '''
from .common import result

PROVIDER = 'extra_cloud'
LABEL = 'Extra Cloud'


def build_collector():
    def collect():
        return result(PROVIDER, [], 'everything', [])
    return collect
'''


@pytest.fixture
def no_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PATH, str(tmp_path / 'absent.yaml'))
    monkeypatch.delenv('AWS_PROFILE', raising=False)
    return tmp_path


def test_missing_file_yields_defaults(no_user_config):
    cfg = load_config()
    assert cfg['ec2'] == {'profiles': ['default'], 'cache_seconds': 300}
    assert cfg['slurm'] == {'hosts': [], 'user': None, 'control_host': None}
    assert cfg['broker'] == {'command': [], 'python': None}
    assert cfg['extra_providers_dir'].endswith(str(Path('compute-rentals') / 'providers'))


def test_aws_profile_env_is_the_default_profile(no_user_config, monkeypatch):
    monkeypatch.setenv('AWS_PROFILE', 'team')
    assert load_config()['ec2']['profiles'] == ['team']


def test_env_path_override_merges_and_keeps_unknown_sections(tmp_path, monkeypatch):
    pytest.importorskip('yaml')  # YAML syntax below; the worker runtime has only the JSON fallback
    path = tmp_path / 'cr.yaml'
    path.write_text(
        'extra_providers_dir: ~/my-providers\n'
        'ec2:\n  profiles: [prod, dev]\n'
        'slurm:\n  hosts: "gpu[0-2]"\n  user: ops\n  control_host: gpu1\n'
        'broker:\n  command: [vault, exec, "{provider}", --]\n'
        'my_extra: {endpoint: x}\n'
    )
    monkeypatch.setenv(ENV_PATH, str(path))
    cfg = load_config()
    assert cfg['ec2'] == {'profiles': ['prod', 'dev'], 'cache_seconds': 300}
    assert cfg['slurm'] == {'hosts': ['gpu0', 'gpu1', 'gpu2'], 'user': 'ops', 'control_host': 'gpu1'}
    assert cfg['broker']['command'] == ['vault', 'exec', '{provider}', '--']
    assert cfg['my_extra'] == {'endpoint': 'x'}
    assert cfg['extra_providers_dir'] == str(Path('~/my-providers').expanduser())


@pytest.mark.parametrize('body', [
    'ec2: {profiles: [""]}',
    'ec2: {cache_seconds: -1}',
    'slurm: {hosts: [a, b], control_host: c}',
    'slurm: {hosts: [a], user: 3}',
    '- not a mapping',
])
def test_invalid_config_raises(tmp_path, body):
    path = tmp_path / 'cr.yaml'
    path.write_text(body)
    with pytest.raises(ValueError):
        load_config(path)


def test_ec2_and_slurm_collectors_use_config(no_user_config):
    from providers.ec2 import EC2Collector
    cfg = load_config()
    cfg['extra_providers_dir'] = str(no_user_config / 'none')
    cfg['ec2']['profiles'] = ['p1', 'p2']
    collectors = build_collectors(cfg)
    assert set(collectors) == BUILTINS
    assert collectors['ec2'].__self__.profiles == ['p1', 'p2']
    assert EC2Collector().profiles == ['default']
    assert collectors['slurm']()['status'] == 'unconfigured'


def test_missing_extras_dir_is_ignored(tmp_path):
    assert load_extra_providers(tmp_path / 'nope') == ({}, {})


def test_good_extra_module_loads_with_label(tmp_path):
    (tmp_path / 'extra_cloud.py').write_text(GOOD)
    (tmp_path / 'test_extra_cloud.py').write_text('raise SystemExit("tests are never loaded")')
    (tmp_path / '_helper.py').write_text('raise SystemExit("private helpers are never loaded")')
    collectors, labels = load_extra_providers(tmp_path)
    assert set(collectors) == {'extra_cloud'}
    assert labels == {'extra_cloud': 'Extra Cloud'}
    out = collectors['extra_cloud']()
    assert out['provider'] == 'extra_cloud' and out['status'] == 'ok'
    assert {'provider', 'machines', 'status', 'error', 'scope', 'observed_at'} <= set(out)


@pytest.mark.parametrize('source', [
    'raise RuntimeError("import boom")',
    'PROVIDER = "broken"\ndef build_collector():\n    raise RuntimeError("build boom")',
    'PROVIDER = "broken"\ndef build_collector():\n    return 42',
    'PROVIDER = "Bad Name!"\ndef build_collector():\n    return lambda: None',
])
def test_broken_extra_module_reports_error_without_crashing(tmp_path, source, caplog):
    (tmp_path / 'broken.py').write_text(source)
    (tmp_path / 'extra_cloud.py').write_text(GOOD)
    collectors, _ = load_extra_providers(tmp_path)
    assert set(collectors) == {'broken', 'extra_cloud'}
    out = collectors['broken']()
    assert out['status'] == 'error'
    assert out['machines'] == []
    assert 'boom' not in str(out)
    assert collectors['extra_cloud']()['status'] == 'ok'
    assert 'failed to load' in caplog.text


def test_extra_module_cannot_shadow_a_builtin(tmp_path, no_user_config):
    (tmp_path / 'fake_vast.py').write_text(GOOD.replace("'extra_cloud'", "'vast'"))
    cfg = load_config()
    cfg['extra_providers_dir'] = str(tmp_path)
    collectors = build_collectors(cfg)
    assert set(collectors) == BUILTINS | {'fake_vast'}
    assert type(collectors['vast'].__self__).__module__ == 'providers.vast'
    assert collectors['fake_vast']()['status'] == 'error'


def test_build_collectors_merges_extras(tmp_path, no_user_config):
    (tmp_path / 'extra_cloud.py').write_text(GOOD)
    cfg = load_config()
    cfg['extra_providers_dir'] = str(tmp_path)
    assert set(build_collectors(cfg)) == BUILTINS | {'extra_cloud'}

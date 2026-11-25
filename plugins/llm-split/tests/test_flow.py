"""Flow-YAML writer regressions and all-home failure atomicity."""
import re

import pytest
import yaml

from test_core import CONFIG, NOW, core, weights_in


def flow(width):
    data = yaml.safe_load(CONFIG)
    data['unrelated'] = 'weight: 1'
    return '# keep é and weight: 123\r\n' + yaml.safe_dump(
        data, default_flow_style=True, sort_keys=False, width=width
    ).replace('\n', '\r\n') + '# keep 456\r\n'


@pytest.mark.parametrize('width', [80, 100000])
def test_flow_grow_shrink_exact_bytes(tmp_path, width):
    path = tmp_path / 'config.yaml'
    original = flow(width).encode()
    path.write_bytes(original)
    for weights in ({'anthropic': 16, 'openai-codex': 58, 'xai-oauth': 25},
                    {'anthropic': 1, 'openai-codex': 1, 'xai-oauth': 1},
                    {'anthropic': 98, 'openai-codex': 1, 'xai-oauth': 1}):
        assert core.write_weights(tmp_path, weights, NOW)
        actual = path.read_bytes()
        # Exact byte oracle, replacing only genuine flow route weight tokens.
        expected = original
        for provider, weight in weights.items():
            expected = re.sub(rb'(provider: ' + provider.encode() + rb',.*?weight: )1(?=[,}])',
                              lambda m: m[1] + str(weight).encode(), expected, flags=re.S)
        assert actual == expected
        assert weights_in(path)['deep'] == weights
        assert core.write_weights(tmp_path, weights, NOW) is False
    assert next(tmp_path.glob('*.pre-llm-split-*')).read_bytes() == original


def mixed(tmp_path):
    (tmp_path / 'config.yaml').write_text(CONFIG)
    profile = tmp_path / 'profiles' / 'alpha'
    profile.mkdir(parents=True)
    (profile / 'config.yaml').write_bytes(flow(100000).encode())
    return [tmp_path / 'config.yaml', profile / 'config.yaml']


@pytest.mark.parametrize('malformed', [False, True])
def test_invalid_later_home_does_not_mutate_earlier(tmp_path, malformed):
    paths = mixed(tmp_path)
    if malformed:
        paths[1].write_bytes(b'model_routing: [unterminated')
    else:
        paths[1].write_bytes(paths[1].read_bytes().replace(b'weight: 1,', b'weight: nope,', 1))
    before = [p.read_bytes() for p in paths]
    with pytest.raises((ValueError, yaml.YAMLError)):
        core.apply(tmp_path, 'manual', {'anthropic': 16, 'openai-codex': 58, 'xai-oauth': 25}, NOW)
    assert [p.read_bytes() for p in paths] == before
    assert not core.state_path(tmp_path).exists()
    assert not list(tmp_path.rglob('*.pre-llm-split-*'))


@pytest.mark.parametrize('failure', ['config', 'state'])
def test_write_failure_rolls_back_all_configs(tmp_path, monkeypatch, failure):
    paths = mixed(tmp_path)
    before = [p.read_bytes() for p in paths]
    replace = core._replace_bytes
    def fail(path, data):
        if path == paths[1] and data != before[1]:
            raise OSError('injected write failure')
        replace(path, data)
    if failure == 'config':
        monkeypatch.setattr(core, '_replace_bytes', fail)
    else:
        def fail_state(*args):
            raise OSError('injected state failure')
        monkeypatch.setattr(core, '_write_state', fail_state)
    with pytest.raises(OSError, match='injected'):
        core.apply(tmp_path, 'manual', {'anthropic': 16, 'openai-codex': 58, 'xai-oauth': 25}, NOW)
    assert [p.read_bytes() for p in paths] == before
    assert not core.state_path(tmp_path).exists()


def test_rollback_failure_is_explicit(tmp_path, monkeypatch):
    paths = mixed(tmp_path)
    replace = core._replace_bytes
    calls = 0
    def fail(path, data):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError('injected disk failure')
        replace(path, data)
    monkeypatch.setattr(core, '_replace_bytes', fail)
    with pytest.raises(RuntimeError, match='ROLLBACK FAILED'):
        core.apply(tmp_path, 'manual', {'anthropic': 16, 'openai-codex': 58, 'xai-oauth': 25}, NOW)
    assert not core.state_path(tmp_path).exists()

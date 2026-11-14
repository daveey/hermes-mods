"""The accelerated decoder must retain stdlib JSON snapshot semantics."""
import json
import math
import pytest
from plugins.runners import service

@pytest.mark.parametrize('text', [
    '{"x":123456789012345678901234567890}',
    '{"x":-0.0}', '{"x":1e400}', '{"x":NaN}',
    '{"x":Infinity}', '{"x":-Infinity}', '{"x":"\\ud800"}',
    '{"x":1,"x":2}', '{"x":[true,null,"é"]}',
])
def test_snapshot_json_semantics(text):
    expected = json.loads(text)
    actual = service._decode_snapshot(text)
    if isinstance(expected['x'], float) and math.isnan(expected['x']):
        assert math.isnan(actual['x'])
    else:
        assert actual == expected
        assert type(actual['x']) is type(expected['x'])

@pytest.mark.parametrize('text', ['{', '{"x":01}', '{"x":undefined}', '[1,]'])
def test_invalid_snapshot_retains_json_error(text):
    with pytest.raises(json.JSONDecodeError):
        service._decode_snapshot(text)


def test_optional_dependency_fallback(monkeypatch):
    monkeypatch.setattr(service, 'msgspec', None)
    assert service._decode_snapshot('{"x": [1, null]}') == {'x': [1, None]}


def test_accelerator_is_installed_and_used(monkeypatch):
    assert service.msgspec is not None, 'runtime/test dependency must be installed'
    def forbidden(*args, **kwargs):
        raise AssertionError('ordinary JSON must use the accelerated decoder')
    monkeypatch.setattr(service.json, 'loads', forbidden)
    assert service._decode_snapshot('{"x": [1, null]}') == {'x': [1, None]}

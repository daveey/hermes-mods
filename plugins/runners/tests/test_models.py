"""Offline fictional contract validation tests; imports stay local for RED."""
import copy
import json
from pathlib import Path

import pytest

FIXTURES = json.loads((Path(__file__).parents[1] / 'contracts/fixtures.json').read_text())


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_contract_fixture(name):
    from plugins.runners.models import validate
    result = validate(name, FIXTURES[name])
    assert result == FIXTURES[name]
    assert result is not FIXTURES[name]


@pytest.mark.parametrize('name,field,value', [
    ('Owner', 'gateway_id', 'not-uuid'),
    ('Observation', 'collected_at', 'not-time'),
    ('Host', 'password', 'SECRET_DO_NOT_ECHO'),
    ('Owner', 'owner_epoch', True),
])
def test_strict_validation_no_values(name, field, value):
    from plugins.runners.models import ModelError, validate
    data = copy.deepcopy(FIXTURES[name]); data[field] = value
    with pytest.raises(ModelError) as error:
        validate(name, data)
    assert str(value) not in str(error.value)
    assert 'SECRET_DO_NOT_ECHO' not in repr(error.value)


def test_unknown_definition_and_non_json_are_safe():
    from plugins.runners.models import ModelError, validate
    for name, data in [('SECRET', {}), ('EmptyRequest', {'x': float('nan')}), ('Owner', {1: 'SECRET'})]:
        with pytest.raises(ModelError) as error:
            validate(name, data)
        assert 'SECRET' not in str(error.value)

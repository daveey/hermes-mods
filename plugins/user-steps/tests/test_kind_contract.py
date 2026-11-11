"""Producer discovery must expose the same kinds that validation accepts."""

import pytest
from test_withdrawal import ROOT, core, complete_request, load_module

KINDS = ["approval", "choice", "input", "external_step"]


def test_producer_schema_exposes_kind_enum():
    plugin = load_module("kind_contract_plugin", ROOT / "__init__.py")
    request = plugin.PRODUCER_SCHEMA["parameters"]["properties"]["request"]
    assert request["properties"]["kind"]["enum"] == KINDS


@pytest.mark.parametrize("kind", KINDS)
def test_documented_kinds_are_accepted(kind):
    request = complete_request()
    request["kind"] = kind
    assert core.validate_action(request)["kind"] == kind


@pytest.mark.parametrize("kind", ["decision", "question"])
def test_invalid_kind_explains_valid_values(kind):
    request = complete_request()
    request["kind"] = kind
    with pytest.raises(core.ActionError) as exc:
        core.validate_action(request)
    assert all(value in str(exc.value) for value in KINDS)
    assert "choice" in str(exc.value)

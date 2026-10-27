"""Synthetic metadata only; no network, credentials, live state or authorization.

Set PYTHONPATH to a core checkout to exercise the optional real client/wire test.
"""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "connector_status_adapter_test", Path(__file__).parents[1] / "connector_status_adapter.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)
TARGET = {"connection_id": "acct_test-1", "connector": "sample"}


def row(**updates):
    return {"connectionId": "acct_test-1", "connector": "sample", "status": "active",
            "active": True, "disabled": False, "label": "PRIVATE LABEL",
            "alias": "PRIVATE ALIAS", "statusReason": "PRIVATE REASON",
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
            **updates}


class Source:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def account_status(self, key, *, timeout):
        self.calls.append((key, timeout))
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_fresh_exact_read_and_minimal_metadata():
    source = Source(row())
    result = adapter.read_account(TARGET, client_factory=lambda: source)
    assert result == {"target": TARGET, "status": "active", "retired": False,
                      "available": True, "lifecycle_supported": False}
    assert source.calls == [("acct_test-1", 2.0)]
    assert "PRIVATE" not in repr(result)
    source.value = row(status="revoked")
    assert adapter.read_account(TARGET, client_factory=lambda: source)["retired"]
    assert len(source.calls) == 2


@pytest.mark.parametrize("status", ["pending", "failed", "expired", "inactive"])
def test_nonterminal_status_is_not_revocation_or_permission(status):
    source = Source(row(status=status, active=False))
    result = adapter.read_account(TARGET, client_factory=lambda: source)
    assert not result["retired"] and not result["available"]
    gate = adapter.ConnectorStatusAdapter(lambda context: TARGET, client_factory=lambda: source)
    assert gate.preflight({})["reason"] == "connector_unavailable"


@pytest.mark.parametrize("updates,reason", [
    ({}, "connector_active"),
    ({"status": "revoked"}, "connector_retired"),
    ({"disabled": True}, "connector_retired"),
    ({"active": False}, "connector_unavailable"),
])
def test_suppression_never_authorizes(updates, reason):
    gate = adapter.ConnectorStatusAdapter(lambda context: TARGET,
                                         client_factory=lambda: Source(row(**updates)))
    held = gate.preflight({"verified": True, "explicit_reopen": True})
    assert held["action"] == "block" and held["reason"] == reason
    assert "not execution authorization" in held["message"]
    assert "PRIVATE" not in repr(held)


@pytest.mark.parametrize("value", [None, [], {}, row(connectionId="other"),
    row(connector="Sample"), row(status="removed"), row(status=[]),
    row(active=1), row(disabled="false"), RuntimeError("PRIVATE FAILURE")])
def test_unknown_missing_mismatched_and_failures_hold_without_leaking(value):
    source = Source(value)
    with pytest.raises(adapter.ConnectorEvidenceUnavailable) as error:
        adapter.read_account(TARGET, client_factory=lambda: source)
    assert "PRIVATE" not in str(error.value)
    gate = adapter.ConnectorStatusAdapter(lambda context: TARGET, client_factory=lambda: source)
    assert gate.preflight({})["reason"] == "connector_unavailable"
    assert "PRIVATE" not in adapter.capability_summary(TARGET, client_factory=lambda: source)


@pytest.mark.parametrize("key", ["../other", "a/b", "a?x", "a#x", "%2F", " a", "a ", "", None, "a" * 257])
def test_reject_unsafe_ids_before_io(key):
    source = Source(row())
    with pytest.raises(adapter.ConnectorEvidenceUnavailable):
        adapter.read_account({**TARGET, "connection_id": key}, client_factory=lambda: source)
    assert not source.calls


def test_no_default_binding_or_model_inference():
    source = Source(row())
    gate = adapter.ConnectorStatusAdapter(lambda context: None, client_factory=lambda: source)
    assert gate.preflight({**TARGET, "draft": "Reconnect sample", "status": "revoked"}) is None
    assert source.calls == []
    with pytest.raises(TypeError):
        adapter.ConnectorStatusAdapter(None)


def test_resolver_failure_and_bad_binding_hold():
    def fail(context):
        raise RuntimeError("PRIVATE")
    for resolver in (fail, lambda context: {}, lambda context: {**TARGET, "revision": 1}):
        gate = adapter.ConnectorStatusAdapter(resolver, client_factory=lambda: Source(row()))
        assert gate.preflight({})["reason"] == "connector_unavailable"


def test_no_active_snapshot_or_model_claim_reopens():
    source = Source(row(status="revoked"))
    gate = adapter.ConnectorStatusAdapter(lambda context: TARGET, client_factory=lambda: source)
    assert gate.preflight({})["reason"] == "connector_retired"
    source.value = row(revision=999, explicit_reopen=True)
    # Even after an apparent active transition, no release of the connection ask.
    assert gate.preflight({"explicit_reopen": True})["action"] == "block"
    with pytest.raises(adapter.ConnectorEvidenceUnavailable, match="monotonic revision"):
        adapter.read_lifecycle({"revision": 999, "updatedAt": "2099-01-01"})


@pytest.mark.parametrize("updates,fragment", [
    ({}, "active"), ({"disabled": True}, "retired"), ({"status": "expired"}, "not currently available")])
def test_capability_summary_is_sanitized(updates, fragment):
    text = adapter.capability_summary(TARGET, client_factory=lambda: Source(row(**updates)))
    assert fragment in text and "PRIVATE" not in text and "acct_test" not in text


def test_real_client_and_wire_use_only_exact_get(monkeypatch):
    client_module = pytest.importorskip("tools.connectors.gateway.client")
    calls = []

    class Reply:
        status_code = 200
        headers = {}
        def json(self):
            # A new field cannot silently become a trusted lifecycle revision.
            return row(revision=99, explicit_reopen=True)

    class Transport:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return Reply()

    monkeypatch.setattr(client_module, "log_connector_outcome", lambda *a, **k: None)
    monkeypatch.setattr(client_module, "connector_headers", lambda *a, **k: {})
    client = client_module.ConnectorClient(
        transport=Transport(), endpoint_resolver=lambda: "https://connector.invalid",
        header_provider=lambda url: {"X-Synthetic-Auth": "fixture"})
    result = adapter.read_account(TARGET, client_factory=lambda: client)
    assert result["available"] and not result["lifecycle_supported"]
    assert "revision" not in result
    assert len(calls) == 1
    method, url, options = calls[0]
    assert (method, url) == ("GET", "https://connector.invalid/v1/connectors/accounts/acct_test-1")
    assert options["json"] is None and options["timeout"] == 2.0

"""Read-only connector account evidence for F3; never execution authorization.

The production source is ConnectorClient.account_status, NOT search's connected
flag, the toolkit list, an account alias, or a model-supplied status. Installed
code must supply a binding from its authoritative workflow store. There is no
such general ask-to-account binding in today's connector wire contract; this
module deliberately does not infer one from draft text or tool arguments.

The current ConnectorAccount wire has connectionId/connector/status/active/
disabled, but NO authority-owned monotonic revision or explicit reopen event.
createdAt/updatedAt are not revisions. Therefore status evidence is useful for
capability reporting and immediate suppression, but cannot be registered as a
lifecycle.Record. All managed asks stay held pending that missing contract;
this module never clears durable retirement or authorizes/reinitiates a link.
No registration or import-time I/O: the parent plugin owns integration.
"""
from __future__ import annotations

import copy
import re
from typing import Callable

_STATUSES = frozenset({"pending", "active", "failed", "expired", "revoked", "inactive"})
# account_status interpolates this value unquoted into a URL path. Reject rather
# than repair keys outside this conservative safe subset (including . and %).
_PATH_KEY = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_TIMEOUT = 2.0  # Client retries once; this is per HTTP attempt, not a wall deadline.
LIMITATION = (
    "ConnectorAccount has no authority-owned monotonic revision or trusted "
    "explicit-reopen event; timestamps cannot supply lifecycle evidence."
)


class ConnectorEvidenceUnavailable(ValueError):
    """Safe error: never include source payloads, URLs, credentials or identities."""


def _client():
    # Resolve runtime authentication/profile each invocation, never cache clients
    # across gateway profile changes. This import does not initiate authorization.
    from tools.connectors.gateway.client import ConnectorClient
    return ConnectorClient()


def _binding(value):
    if not isinstance(value, dict) or set(value) != {"connection_id", "connector"}:
        raise ConnectorEvidenceUnavailable("Missing exact connector binding")
    key, connector = value["connection_id"], value["connector"]
    if not isinstance(key, str) or not _PATH_KEY.fullmatch(key):
        raise ConnectorEvidenceUnavailable("Unsupported connector account key")
    if not isinstance(connector, str) or not connector.strip() or len(connector) > 256:
        raise ConnectorEvidenceUnavailable("Invalid connector identity")
    return dict(value)  # Never normalize identity.


def read_account(binding: dict, *, client_factory: Callable = _client) -> dict:
    """Fresh authenticated GET for one exact, trusted account binding.

    Returns metadata only. Missing rows are unknown, not proof of revocation.
    ``available`` is a capability hint, not consent or operation entitlement.
    Client injection is for trusted installed code/tests, never model tools.
    """
    target = _binding(binding)
    try:
        row = client_factory().account_status(target["connection_id"], timeout=_TIMEOUT)
    except Exception:
        raise ConnectorEvidenceUnavailable("Connector status read unavailable") from None
    if not isinstance(row, dict):
        raise ConnectorEvidenceUnavailable("Connector account not found")
    if row.get("connectionId") != target["connection_id"] or row.get("connector") != target["connector"]:
        raise ConnectorEvidenceUnavailable("Connector status identity mismatch")
    status = row.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        raise ConnectorEvidenceUnavailable("Unknown connector status")
    if type(row.get("active")) is not bool or type(row.get("disabled")) is not bool:
        raise ConnectorEvidenceUnavailable("Invalid connector account flags")
    retired = status == "revoked" or row["disabled"]
    return {"target": target, "status": status, "retired": retired,
            "available": status == "active" and row["active"] and not retired,
            "lifecycle_supported": False}


def read_lifecycle(identity: dict):
    """Explicitly refuse the current source's insufficient lifecycle contract.

    Do not register this as a working lifecycle reader. Even a future extra
    ``revision`` field is ignored by today's core wire parser; support requires
    a reviewed source contract and binding, not guessing from arbitrary extras.
    """
    raise ConnectorEvidenceUnavailable(LIMITATION)


def capability_summary(binding: dict, *, client_factory: Callable = _client) -> str:
    """Privacy-safe exact-account hint; never expose label/alias/statusReason."""
    try:
        result = read_account(binding, client_factory=client_factory)
    except ConnectorEvidenceUnavailable:
        return "Connector account status unavailable; agent-owned recovery, not a reconnect ask."
    if result["retired"]:
        return "Exact connector account retired; do not ask to reconnect or replace it."
    if result["available"]:
        return "Exact connector account active; use supported tools subject to normal authorization."
    return "Exact connector account not currently available; no reconnect or reopen authority established."


def _hold(reason):
    text = {
        "connector_retired": "This exact connector account is retired. Do not ask to reconnect or replace it.",
        "connector_active": "This exact connector account is already active. Do not repeat its connection ask.",
        "connector_unavailable": "Connector lifecycle cannot be reconciled. Use agent-owned recovery, not a new human ask.",
    }[reason]
    message = "ask-gate: " + text + " This is not execution authorization and does not satisfy OS authentication."
    return {"action": "block", "reason": reason, "message": message, "retry_message": message}


class ConnectorStatusAdapter:
    """Trusted integration seam for exact connection asks only.

    resolve(context) must read authoritative runtime workflow scope and return
    {connection_id, connector}, or None for unrelated actions (including OS
    authentication). Do NOT implement it by trusting context/tool/model identity
    fields. No default resolver exists because current core has no such binding.
    Each managed ask is freshly read. Pending/expired/failed/inactive are held,
    not mislabeled revoked or silently allowed to reopen a prior retirement.
    """

    def __init__(self, resolve: Callable, *, client_factory: Callable = _client):
        if not callable(resolve) or not callable(client_factory):
            raise TypeError("Trusted connector callbacks required")
        self.resolve = resolve
        self.client_factory = client_factory

    def preflight(self, context: dict) -> dict | None:
        try:
            binding = self.resolve(copy.deepcopy(context))
            if binding is None:
                return None  # Ordinary validation continues; never authorization.
            result = read_account(binding, client_factory=self.client_factory)
        except Exception:
            return _hold("connector_unavailable")
        if result["retired"]:
            return _hold("connector_retired")
        if result["available"]:
            return _hold("connector_active")
        return _hold("connector_unavailable")

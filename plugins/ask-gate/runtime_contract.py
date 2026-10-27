"""Registration-time compatibility check, not proof of actual final delivery.

The installed core must explicitly advertise PRE_FINAL_GATE_CAPABILITIES in
agent.pre_final_gate. Version 1 requires ``replace`` (including consumption by
core's buffered-final path), ``accepts_hold_budget`` (including zero), and
``needs_buffer`` (buffer before evaluating a final candidate), and
``post_transform_release`` (release only after output transforms). These flags do
not certify every transport, unbuffered path, adapter, or lifecycle source.

Call require_runtime_contract() before installing adapters or registering ANY
hooks. Never infer support from a hook name, callback signature, source text,
revision string, or the mere existence of GateDecision. Do not import run_agent
here: registration must work without initializing the agent runtime.
"""
from __future__ import annotations

from collections.abc import Mapping
import importlib


CONTRACT_ATTRIBUTE = "PRE_FINAL_GATE_CAPABILITIES"
CONTRACT_VERSION = 1
REQUIRED_CAPABILITIES = ("replace", "accepts_hold_budget", "needs_buffer", "post_transform_release")


class RuntimeContractError(RuntimeError):
    """The installed core cannot advertise the required ask-gate contract."""


def _failure(reason: str) -> RuntimeContractError:
    return RuntimeContractError(
        "ask-gate registration refused: " + reason + ". "
        "Install a compatible Hermes core advertising "
        "agent.pre_final_gate.PRE_FINAL_GATE_CAPABILITIES version 1 "
        "with replace, accepts_hold_budget, needs_buffer, and post_transform_release set to True. "
        "No ask-gate hooks or adapters should be installed before this check."
    )


def require_runtime_contract() -> None:
    """Raise clearly on absent/malformed/incompatible core advertisement.

    Additional capability keys are allowed for additive evolution; unknown
    versions are not. Values must be literal True, not truthy substitutes.
    This is compatibility negotiation only, not a runtime delivery test.
    """
    try:
        runtime = importlib.import_module("agent.pre_final_gate")
    except Exception as exc:
        raise _failure("could not import agent.pre_final_gate") from exc
    contract = getattr(runtime, CONTRACT_ATTRIBUTE, None)
    if not isinstance(contract, Mapping):
        raise _failure("missing or malformed " + CONTRACT_ATTRIBUTE)
    version = contract.get("version")
    if type(version) is not int or version != CONTRACT_VERSION:
        raise _failure("unsupported or malformed contract version")
    missing = [name for name in REQUIRED_CAPABILITIES if contract.get(name) is not True]
    if missing:
        raise _failure("missing or invalid capabilities: " + ", ".join(missing))

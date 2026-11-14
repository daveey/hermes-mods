# Quiet admission preflight and fail-closed dispatch test pins

Mirrored entries: 3858, 3861, 3867.

## Behavior

`quiet-hold` asks the gateway’s read-only quiet-observe-check before taking the quiet lease or spending cooldown. An answered refusal exits 3 with the check document and starts no observer. If the gateway is unavailable or lacks the verb, hold preserves prior behavior and the subsequent start remains authoritative.

Tests pin host-file refusal parity and dispatch accounting’s requirement that literal hold fields, and non-dataclass results, cannot masquerade as all-quiet deferrals.

## How it works

The existing host-file preflight remains first, with its stricter freshness margin and exit 78. The new `_gateway_preflight` shares `_check_doc`, admission evaluation and JSON printing with the --check command. Its gateway freshness window is start’s own 120 seconds without the host-file margin. Check is bookkeeping-only: no lease, cooldown or registry epoch reservation.

The dispatch tests construct each literal hold field independently rather than deriving the test set only from the production classification, and add a non-dataclass result. Host-file parity cases cover admission and live-pid drain-hold markers under idle/draining windows.

## Code

- `gateway/quiet_control.py`
- `hermes_cli/kanban_quiet.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_quiet_gates_dispatch.py`: Asserts each literal hold field stays in the hold set rather than compatible fields, disqualifies all_quiet_deferred alone or mixed, and rejects a non-dataclass result carrying quiet-deferred data.
- `tests/gateway/test_quiet_control.py`: Compares preflight and actual start refusals for host-file window states and update markers, including update_hold reason precedence.
- `tests/hermes_cli/test_code_attestation_relaunch.py`: Checks gateway preflight refusal leaves no lease/cooldown/observer and no registry-epoch change; an admitted relaunch acquires and starts; unsupported/no-answer checks proceed with the prior behavior.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Preflight is advisory and can race the authoritative start. An unsupported check deliberately does not fail closed, whereas a supported negative answer does. The dispatch and host-file patches here are test-only; they do not change all_quiet_deferred or start policy.

## Plugin?

No. Admission checks must compose with the gateway’s quiet lease and start protocol.

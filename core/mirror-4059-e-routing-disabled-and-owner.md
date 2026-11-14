# Enabled-credential walls and pre-reference routing owner

Mirror ordinals: 3970, 3989.

## Behavior

Disabled credentials count as neither available capacity nor evidence of quota/auth walls. Pre-turn reference expansion carries an authorized local chat routing owner before TurnRunner installs its scope.

## How it works

Quota scans and auth-churn classification use the same enabled_entries policy as selection. All-disabled stays unknown rather than a proven quota wall. Policy runtime lookup enters routing_session(current owner or source chat ID), preserving an ambient owner and restoring scope even on errors. A no-session wall-deferral diagnostic is elevated to ERROR and gateway tests reject such diagnostics.

## Code

- `agent/model_routing_unavailable.py`
- `agent/model_routing_walls.py`
- `gateway/run_turn_prepare.py`

## Tests

- `tests/agent/test_quota_unavailable_auth_bench.py`: Disabled healthy, earlier-wall and auth-benched rows do not poison enabled reset selection; all-disabled is not quota/auth churn; runtime verification maps enabled exhaustion to retryable worker exit 75; reenabling the same pool immediately restores room.
- `tests/gateway/test_walled_reference_lookup.py`: Real inbound file-reference expansion retains its payload under an injected wall, emits no no-session failure, and restores routing context. Controlled successful/raising policy lookup preserves an existing ambient owner.
- `tests/agent/test_walled_default_pin.py`: Pins ERROR severity for the no-session case.
- `tests/gateway/conftest.py`: Autouse guard rejects no-session routing-wall diagnostics captured during setup or call across gateway tests.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

No-session is a binding error, not a successful quota deferral. These tests do not establish post-deployment wall behavior or remote reference lookups.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

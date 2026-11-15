# Quiet admission and relaunch attestation boundary tests

Mirror snapshot 4059; covered ordinals: 3925, 3942.

## Behavior

These test-only patches strengthen existing fail-closed relaunch and quiet-admission contracts. Untrusted relaunch module locations, unreadable/unknown code inventories, malformed admission booleans, and inconsistent admission reasons cannot grant an observation lease. An unavailable advisory preflight query may proceed to the observer, whose authoritative start still decides.

## How it works

Tests exercise `hermes_cli/code_attestation.py`, `gateway/quiet_control.py`, and `hermes_cli/kanban_quiet.py` through their existing interfaces. They distinguish a refused check, which must not acquire a lease or consume cooldown, from an unavailable preliminary query, which follows the existing observer-start route and may consume cooldown. Bootstrap ledger capture must survive replacement of the globally installed ledger.

## Code

These ordinals change only the test files listed below; the existing implementation boundaries are identified under How it works.

### Mirrored commits

- 3925: `344cee143a414b4c2622ac48478c20375377e0ef` — Pins quiet code-ledger and relaunch-prefix attestation refusals.
- 3942: `21730eee4998609f7a39da7e0039910a08baf582` — Pins inconsistent quiet admission and unavailable preflight behavior.

## Tests

- `tests/gateway/test_quiet_attestation_boundaries.py`: relaunch prefix modules must be in stdlib, not outside it or under package directories. Unreadable and unlatched unknown code inventory refuse both start/check and return no observer token. Nonboolean `admitted` values become unavailable/refused; registry binding preserves bootstrap unknown evidence after installed-ledger replacement.
- `tests/hermes_cli/test_kanban_quiet.py`: `admitted=True` plus refusal reasons blocks both check/hold, never starts the observer, leaves no active lease/cooldown, and permits immediate acquisition. A raising query proceeds to the observer, preserves its failure result, reports the diagnostic, releases the lease, and records cooldown.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

Do not collapse malformed/refused responses and transport exceptions into one policy: the tested preflight is advisory when unavailable, but an explicit refusal remains binding. These tests do not prove a complete quiet observation of any running host.

## Plugin?

No. Attestation, lease acquisition, and authoritative control checks must stay in their core trust boundary.

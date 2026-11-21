# Open containment authorities and explicit completion custody

Mirror entries: 5107, 5108, 5109.

## Behavior

- New containment episodes refuse a done/archived release authority, locally and remotely when status is available.
- An authority completing while episodes remain open must release them or explicitly list their episode IDs in `metadata.containment_retained` and explain the hold in its summary.
- Worker context exposes authority-owned episodes, including replica-only targets. Placement-settle transitions are exempt; producer release and unsafe-descendant containment semantics remain unchanged.

## How it works

`kanban_containment_authority.py` finds current episode markers and exact stop-event authority policies, merging local custody ahead of sync-shadow data. A local cleared marker defeats a stale replica hold. Lookup is read-only and does not contact the network.

Completion checks acknowledgement before and again inside the write transaction to catch an episode committed between precheck and completion. Core CLI/tool adapters preserve a typed `containment_authority_open` refusal rather than allowing force completion through it. Retained episode IDs are persisted in run metadata. Context output is omitted entirely when no episodes exist.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_containment_authority.py`
- `hermes_cli/kanban_containment_lifecycle.py`
- `hermes_cli/kanban_containment_remote.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_context.py`
- `tools/kanban_containment_tools.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Core tests cover terminal nominations, remote refusal before staging, backward-compatible missing remote status, malformed/stale retention, release versus explicit retain, metadata persistence, replica-only targets, local release precedence, lookup-error handling, producer release, placement-settle exemption, spacing and the precheck/write race. Tool and CLI fixtures cover refusal then retention acceptance, including force handling. REST and live remote authority decisions are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_containment_authority.py`
- `tests/tools/test_kanban_containment_authority.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Compatibility is deliberate: missing remote status does not itself refuse, and lookup errors log then fail open; this is not a complete fail-closed safety proof. Retention is an explicit continuing hold, not release. Containment is not a substitute for a review dependency or review request.

## Why core instead of a plugin

Release policy, completion transactions, worker context and CLI/tool error contracts are core lifecycle responsibilities. A plugin cannot atomically guard completion against a newly committed episode.

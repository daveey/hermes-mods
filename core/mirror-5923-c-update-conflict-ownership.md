# Hub-owned default upstream-conflict reporting

Patches: **5914–5915**.

## Behavior

Default upstream integration conflict ownership belongs to the configured hub, not each worker's local board. A non-hub or unidentified gateway skips filing a local repair card and keeps the fork-only update. Explicit configured observation routing retains its existing behavior and is evaluated before the default-owner restriction.

## How it works

The filing boundary obtains installed gateway identity and uses the canonical hub predicate with placement configuration, including configured aliases. Without confirmed hub identity, `_report` returns no task instead of creating a local integration owner that cannot deduplicate against the authoritative board.

The child command emits a distinct skip marker for this outcome. The parent recognizes the skip only on successful child exit and an exact standalone marker line. Malformed/embedded markers or nonzero exits remain reporting failures, not successful ownership skips. Hub lookup/create serialization and existing deduplication are retained.

CI change classification adds capability identity, hub placement predicate and placement configuration as update dependencies so changes to ownership inputs select upgrade coverage. Follow-up fixtures explicitly supply hub authority where they expect local cards.

## Code

- `hermes_cli/update_upstream_conflict.py`: authority guard and child/parent skip protocol.
- `scripts/ci/classify_changes.py`: update dependency classification.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_update_upstream_conflict.py`: fresh-child non-hub/unknown identities create no local owner, exact success-only skip protocol and filing-boundary hub deduplication.
- `tests/hermes_cli/test_update_upstream_before_pause.py`: worker conflict preserves the fork update without a local repair card or pause gate.
- `tests/hermes_cli/test_update_conflict_reporting.py`: configured-route fixtures remain explicit about authority.
- `tests/hermes_cli/test_update_upstream_conflict_card.py`: conflict-card fixture supplies hub identity for its existing owner/reuse contract.

No update, merge or live integration card was performed in this documentation pass.

## Applying

Replay through `core/README.md` with the preceding upstream-conflict reporting implementation and canonical placement registry. For a port, retain explicit-route precedence and check authority at the actual filing boundary, not only in the parent process. Carry CI dependency routing and fixture authority changes together.

## Gotchas

Unknown identity is not hub authority. A skipped local card is intentional ownership routing, not proof the upstream conflict is resolved. Do not convert a failed child into a skip by matching a substring. This change does not authorize worker-side upstream integration.

## plugin?

Core owns updater subprocess protocol and default conflict-card custody. Existing configurable reporting remains available; no new plugin interface is introduced.

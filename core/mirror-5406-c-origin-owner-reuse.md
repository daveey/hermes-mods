# Origin ledger conclusions after owner-name reuse

Mirror entries: 5259, 5260. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

A reused gateway name does not leave a closed, re-homed card permanently unknown. Any conclusion requires the exact old owner scope to be fresh, readable and missing the card. Offline/unreadable owners remain unknown even if another gateway publishes a copy.

## How it works

For a listed foreign owner, `_reused_owner` considers matching publication identity (board, task ID and creation time), not ID alone. With a live readable absent scope, a matching publication elsewhere drops the obsolete binding; without another publisher, a stored done/archived observation can be final. Open observations and still-published cards remain unchanged. The current gateway's own scope is excluded from this reused-owner rule.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_origin_ledger.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests cover genuine rehoming, ID reuse with different identity, closed absence on a live owner, open/continued publication, offline/unreadable scope, another publisher while the owner is unavailable, exact-scope heartbeat versus weaker liveness, and own-gateway exclusion.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_origin_reused_owner_name.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

A listed name or generic heartbeat is not proof that this exact owner/profile scope was read. Do not turn old closed state into a conclusion while its scope is offline.

## Why core instead of a plugin

The origin ledger owns publication identity and conservative finality rules; a plugin cannot reinterpret its binding without changing downstream readiness semantics.

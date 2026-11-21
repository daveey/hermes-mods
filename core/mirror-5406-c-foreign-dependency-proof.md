# Dependency-delete proof for absent foreign endpoints

Mirror entries: 5236, 5237. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Dependency-delete recovery can validate an endpoint absent from the live partition when authenticated Board evidence and the full replica establish its foreign origin and current identity. It does not import that foreign task or mutate its lifecycle merely to delete a dependency.

## How it works

A transaction-bound read-only replica snapshot captures cursor, endpoint tasks, containment holds and the shadow dependency link. It must match the authenticated head/link. Missing endpoints must have a nonempty foreign `created_on`, valid containment identity/context, a unique replica row with matching integer version, and matching status/lease/containment fields. Any containment hold refuses. The snapshot is included in the preview token and rechecked at evidence and writer boundaries; network requests are outside DB transactions.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_sync_dependency_delete.py`
- `hermes_cli/kanban_sync_dependency_foreign.py`
- `website/docs/user-guide/features/kanban-dependency-delete-recovery.md`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests cover certified release/renewal evidence, preview/apply/settle without task materialization or lifecycle writes, containment refusals, absent/legacy/local-origin defects, replica compare-and-swap changes, endpoint work/lease races, and binding a changed foreign snapshot into a different preview token.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_sync_dependency_foreign.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

An absent own-origin or origin-less task remains missing, not a recoverable foreign endpoint. A stale preview must be regenerated; matching IDs without current version/cursor/context proof are insufficient.

## Why core instead of a plugin

Recovery relies on Board authentication, preview-token binding and live/replica transaction boundaries that a general plugin cannot interpose atomically.

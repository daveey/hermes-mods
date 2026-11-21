# Compression-parent reopen preserves live turn custody

Mirror entries: 5281. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Reopening a compression parent is refused while its compression lineage has a live turn lease, preserving the current owner's valid lease key. After release or stale-owner reclamation it can proceed. Ordinary noncompression reopen behavior is unchanged.

## How it works

Before clearing a compression parent's close stamp, `_reopen_session_conn` invokes transcript write guards with `reject_active_turn_lease=True` and `allow_closed_compression_parent=True`. Clearing that stamp otherwise changes descendant root selection and strands a valid holder. The check and mutation occur under the database transaction so acquisition and reopen serialize.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_state_sessions.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests use temporary state DBs to verify refusal with surviving current owner, released/stale writer fences, success after release/new-key acquisition, stale-row reclaim, unchanged ordinary reopen and concurrent reopen/acquire producing one valid owner.

Relevant source suites/fixtures:

- `tests/hermes_state/test_reopen_compression_parent_turn_lease.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Do not clear the parent stamp to bypass an active or parked turn. This protects lineage identity; it does not merge two independently executing transcripts. Gateway/UI reopening entry paths are untested separately.

## Why core instead of a plugin

Only the session-store transaction can serialize compression lineage mutation with turn-lease admission. A plugin after-reopen hook is too late.

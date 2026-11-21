# Connection-owned containment SQL callback registration

Mirror entries: 5311. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Containment guard/receipt callbacks can be refreshed while another SQL cursor is active on the same connection, without replacing the registered SQLite UDF. Repeated guard/certification installation retains the current verifier and snapshot semantics.

## How it works

A connection-owned callable wraps each SQL callback. The first registration calls `create_function`; subsequent registrations update the wrapper callback in Python. A weak-value registry keyed by connection identity, function name and arity avoids globally retaining connections and prevents a reused connection ID from inheriting a stale registration. SQLite retains the callable until connection close/collection.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_containment_functions.py`
- `hermes_cli/kanban_containment_guard.py`
- `hermes_cli/kanban_containment_replica.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests keep a SELECT cursor active across guard/replica registration and stop-plus-release certification, verify an updated guard verifier actually takes effect, check independent plain connections and released registration bookkeeping after close, and restore snapshot callbacks without SQL replacement. These exercise temporary SQLite connections, not a live sync gateway.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_containment_udf_registration.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

SQLite disallows replacing a UDF while any statement is active, even if that statement does not call the function. Updating semantics must preserve the wrapper lifetime, not simply suppress the SQLite error. Other DB adapters and arbitrary third-party UDF registration paths are untested.

## Why core instead of a plugin

The guard and receipt verifier own these SQL callbacks and connection lifetimes. A plugin cannot safely change their installation or transaction-local verifier semantics afterward.

# HTTP protocol failures use the sync transport error contract

Mirror entries: 5256. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Malformed HTTP status lines and truncated response bodies from the kept-alive Board proxy are normalized to `urllib.error.URLError`, so normal sync attempt/deadline handling sees transport failure rather than an uncaught protocol exception. Complete HTTP responses retain their existing contract.

## How it works

The request path catches `http.client.HTTPException`, records the attempt with start/finish/deadline accounting and wraps the original cause. Idle connection liveness polling uses `poll()` when available, avoiding the descriptor ceiling of POSIX `select()`; platforms without poll retain select. Missing sockets and poll errors are treated as closed.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_assignment_routing.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_hub_transport.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Proxy-refusal source tests feed protocol failures over multiple methods/deadlines and check the URL-error cause plus attempt behavior; complete HTTP response cases preserve the status contract. A live proxy outage and high-descriptor production gateway are untested by this mirror task.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_assignment_routing.py`
- `tests/hermes_cli/test_kanban_sync_hub_proxy_refusal.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

A complete application-level error response is not a malformed transport. This change does not certify retry safety for arbitrary mutations or a successful Board request.

## Why core instead of a plugin

Connection reuse and exception normalization belong to the shared core HTTP client; a plugin around individual callers would not cover the common transport consistently.

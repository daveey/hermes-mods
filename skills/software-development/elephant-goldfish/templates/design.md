# <Feature name>: design

Status: draft | in review | approved
Source revision: <git rev-parse HEAD of the repo this design targets>

## Problem

What is wrong or missing today, for whom, and how we know (evidence).

## Constraints

Hard limits: compatibility, performance, cost, security, policy, deadlines.

## Design

The chosen approach: components, data flow, interfaces. Stay at architecture altitude.

## Decisions

Each decision a builder would otherwise have to make, with the choice and its reason. Include failure policy:
retries, idempotency, ordering, timeouts, concurrency.

| Decision | Choice | Why |
|---|---|---|
| | | |

## Alternatives

Other viable designs and the specific reason each was rejected.

## Non-goals

What this deliberately does not do.

## Source references

Existing files, symbols and contracts this design relies on, as `path:symbol`, each described as it exists now.

## Implementation plan

### Files

- Create `path` - purpose
- Modify `path` - change
- Delete `path` - reason

### Steps

Ordered, each leaving the system buildable and testable, with the command that checks it.

## Invariants

Properties that must hold before and after the change.

## Acceptance

Tests and observable checks fixed before implementation (happy path, edge cases, failure modes).

## Rollback

How to undo the change, including data.

# Exact-membership deduplication for home-overdue repairs

Mirror entries: 5113, 5114, 5115, 5116, 5117.

## Behavior

- A recently closed repair covers the same home and exact overdue-card membership for twelve hours, avoiding a new repair on every tick.
- Membership changes, including shrinking sets, or cooldown expiry allow replacement; a different home never inherits another home's coverage.
- An open covering repair wins over a closed sibling and still receives observations. An archived row with no completion time keeps covering the same set indefinitely.

## How it works

The observer looks for open coverage before recent closed coverage. Newly written bodies begin with a detector-owned JSON membership marker, independent of title/checkpoint prose. Legacy cards retain an anchored overdue-listing fallback. Marker validation and exact set equality avoid title newlines or listing-shaped comments altering membership. Closed lookup uses the home-specific idempotency namespace; the twelve-hour clock is completion time, never an invented archive time.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_pickup_stall_home.py`
- `docs/kanban-home-stall.md`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Home-stall tests cover repeated ticks after close, expiry, changed/growing/shrinking membership, second closed cycles, checkpoint bullets and listing-shaped prose, newline titles, malformed markers, legacy fallback, open-before-closed ordering, undated archives and home-key isolation. The accompanying source guide is corrected. Actual home sleep/hold state, repair execution and natural observer ticks are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_pickup_stall_home.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

This is detector deduplication, not a pickup fix. Same membership means exact equality, not subset containment. Missing archive completion time intentionally has no expiry; do not silently substitute creation time. Existing overdue thresholds and held/revoked/silent predicates are unchanged.

## Why core instead of a plugin

The core observer owns its repair idempotency keys, membership markers and lifecycle timestamps. A plugin cannot reliably coalesce detector filings after their authoritative creation decisions.

# Kanban lanes projection: shared evidence, fresh sources, one spend scan (mirror 5482, 5483, 5490, 5521, 5522)

## Behavior
- A lanes flow projection shares one request-scoped `Evidence` across PR prefetch, card classification and dependency blockers instead of repeating ordered history reads and JSON decoding; the legacy differential path stays intact and nothing is cached across requests.
- `kanban_lanes_sources.py` selects one whole-card source per open card for presentation: only primary boards overlay local rows; a pending (non-rejected) outbox change on the task, its links or its events/runs pins the local copy, otherwise the higher `sync_version` wins (local on ties); a tombstone on either side is final. Accounting stays on the canonical replica and run/event ids are never merged. The lanes CLI `show`/`set` paths and flow use a shared `read_view`; daily-limit `reason()` gained a `provenance_conn` argument.
- Worker-result spend is scanned once per projection (`spent_by_lane`; `spent_today` delegates with identical rounding and per-lane association); each card's PR URL is reused from Evidence. Absent PR evidence is preserved via a `_URL_UNSET` sentinel (5522), so a card with no PR URL is not re-derived from history; 5522's test counts calls of a monkeypatched helper, but the diff does not show which budget it asserts.
- Legacy awaiting PR URL fallback keeps explicit-PR precedence and rejects malformed/non-PR awaiting refs.

## How it works
`kanban_lanes_flow.py`, `kanban_lanes_flow_evidence.py`, `kanban_lanes.py`, `kanban_lanes_sources.py`, `kanban_lanes_cli.py`, `kanban_lane_daily_limits.py`; docs line in `website/docs/user-guide/features/kanban.md`.

## Code
All under `hermes_cli/` as listed.

## Tests
Source: `test_kanban_lanes_flow_projection.py` (by test names: huge histories read and decoded once, shared evidence preserves legacy card semantics, evidence shared between context and snapshot but not across requests, shared parent history loaded once, shared blockers match the legacy oracle), `test_kanban_lanes_flow_urls.py` (legacy URL discovery matches shared evidence, parametrized by kind/ref; explicit PR preferred over awaiting), `test_kanban_lanes_sources.py` (source selection: pending wins else newer revision, archive/tombstone, child-source ownership, pending history and link deletes pin the card, fleet delete is final, malformed fields, rejected/unrelated tables do not pin, primary-mode only, terminal bodies not bulk loaded, held predicate reads the selected source, usage keeps canonical run accounting, absent parent does not trust a stale mirror, CLI and flow share one view, read view releases only its own transactions and replica), `test_kanban_lanes_flow.py` (spend from one scan equals the per-lane scan; evidence projection equals plain projection with PR URLs). Not executed in this mirror pass.

## Applying
Apply in order; 5521–5522 build on 5482/5490.

## Gotchas
The patches add no timing measurement; the reduced-read claims rest on counting tests only.

## plugin?
No.

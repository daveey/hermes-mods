# Kanban lanes: flow JSON, split landing steps, daily admission and spend caps

Mirror entries 5155-5160.

## Behavior

- `hermes kanban lanes flow --json [--window 1h|6h|24h|7d]` reports for every open card a step (intake, waiting, ready, running, review, ci, merge, deploy, test, done; unplaceable landing is `landing_unclassified`), a reason and a source kind, plus per-lane throughput. An unknown window is rejected.
- Per-lane `cards_per_day` and `usd_per_day` (unset = unlimited, set via `lanes set`) hold new cards at the gate with reasons `limit:cards_per_day` / `limit:usd_per_day`, reset at local midnight. Both caps are enforced in planner admissions; configured review/landing-title exemptions under an admitted creator are preserved. A person's explicit admission remains an override and still counts toward `cards_per_day`. Caps reserve admissions, not running slots; lowering a cap does not interrupt admitted work. Spend counts only `usage.cost_usd` on worker-result receipts; token-only receipts add zero, not an inferred price.
- 5156: awaiting is read from the current scheduled event; `changes_requested` supersedes `review_requested`; PR state, review and required checks are read per report under a budget and deduped by URL; missing checks are not green; test is shown only with explicit matching live readback. Daily-limit-only parks are reconsidered by home-owned readiness maintenance, respecting foreign copies, containment, ordinary gates and capacity. Actual admissions are counted, not re-lanes or parked creation; legacy creation events remain compatible.
- 5157: the PR reader uses a 30 s wall budget with concurrent prefetch and parallel view+checks; approval comes from a non-author human's latest APPROVED review on the current head with no CHANGES_REQUESTED; reopened cards leave review; the planner keeps the creator-based cap exemption.
- 5158: first-line explicit verdicts on commit-bound COMMENTED reviews are observed as fleet verdicts (display only; ship authorization unchanged), and a stored creatorless card never inherits the evaluating worker's creator.
- 5159: unknown tenants count against the default lane both at enforcement and in accounting; historical dispatch skips expire at claim/launch/completion/reopen and a running card beats a stale quota skip.
- 5160: the same stored-creator fence applies to the ordinary gate during midnight release; automatic release does not spend the planner admit budget; known failed required checks report as `ci`; strict first-nonempty-line COMMENT verdict contract is documented.

## How it works

- `hermes_cli/kanban_lanes_flow.py`, `kanban_lanes_flow_pr.py`, `kanban_lanes_plan.py`, `kanban_lanes.py`, `kanban_lanes_cli.py`, `kanban_parser.py`.
- `hermes_cli/kanban_lane_daily_limits.py`: counters, midnight reset, release.
- `hermes_cli/kanban_db.py`, `kanban_db_events.py`: event evidence. Docs: `website/docs/user-guide/features/kanban.md`, `configuration.md`.

## Code

- `hermes_cli/kanban_lanes_flow.py`
- `hermes_cli/kanban_lanes_flow_pr.py`
- `hermes_cli/kanban_lane_daily_limits.py`
- `hermes_cli/kanban_lanes.py`
- `hermes_cli/kanban_lanes_plan.py`
- `hermes_cli/kanban_lanes_cli.py`

## Tests

Source tests (not executed in this mirror pass):

- `tests/hermes_cli/test_kanban_lanes_flow.py` (each step/reason/source kind, caps under/over/unset, midnight reset, JSON shape, `lanes set` persistence, unknown window).
- `tests/hermes_cli/test_kanban_lane_daily_limits.py`, `test_kanban_lanes_flow_evidence.py`, `test_kanban_lanes_flow_pr.py` contain persisted admission/release regressions and executable fake-`gh` fixtures, including simulated slow responses within the budget, empty review decision reaching merge, fleet comment verdicts and change-request supersession. These are source coverage, not live GitHub or mirror-run results.
- `tests/hermes_cli/test_kanban_flow_review4.py` and `test_kanban_flow_review5.py` (default cap counting unknown lanes, stale skips, creatorless detector gate, planner budget not spent).

Real GitHub latency and production caps were not exercised.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Caps default to unlimited; nothing changes until a lane sets one.
- Spend is reported-cost only.
- Flow verdicts are display evidence and do not authorize a ship.

## Why core instead of a plugin

Lane admission, planner and event evidence live in the core kanban database and dispatcher; the flow report must read the same transactional state the gate uses.

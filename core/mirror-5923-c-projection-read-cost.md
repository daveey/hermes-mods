# Projection-local reader reuse and skipped upstream enrichment

Patches: **5906, 5916**. These extend existing origin batching and sidebar enrichment rather than introducing a new projection protocol.

## Behavior

Origin projection reuses guarded owner-local database opens within one projection pass, without retaining a read snapshot between borrows. Separately, upstream-dependency presentation avoids acquiring a replica when every displayed card is in a status the graph walk cannot annotate.

## How it works

`kanban_origin_batch.read_pass()` wraps the origin-ledger projection in the existing read-only batch context. Each local borrow rolls back so later borrows can see intervening updates or source uncertainty; handles close at pass exit, including exceptions. `HERMES_ORIGIN_BATCH_READS=0` (also false/off/no) selects the legacy nonbatched path.

`_add_blocked_upstream` first removes stale `blocked_upstream` and `upstream` fields from every card. It builds a pending subset excluding done, archived, blocked and triage. If empty, it returns before replica acquisition or graph SQL. Mixed inputs read roots and walk only eligible cards. Caller-supplied replica custody is unchanged.

## Code

- `hermes_cli/kanban_origin_batch.py`: shared enable check and projection read-pass context.
- `hermes_cli/kanban_origin_ledger.py`: context bounds around the projection.
- `hermes_cli/kanban_session_activity_card_details.py`: eligible-subset graph enrichment.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_kanban_origin_local_read_pass.py`: changed/missing evidence between borrows, enabled/disabled open counts, fresh subsequent passes and cleanup after failure.
- `tests/hermes_cli/test_kanban_upstream_skipped_rows.py`: skipped-only inputs execute no SQL or replica acquisition, clear stale fields, preserve supplied handles; mixed roots exclude skipped cards; root-query failures still clear old presentation.

These are fixture read-cost and semantics checks, not deployed CPU or latency measurements.

## Applying

Replay through the documented snapshot recipe in `core/README.md`. For a port, retain the existing batch manager's rollback/close behavior and the upstream walk's status semantics; simply holding a transaction open would change visibility. These are incremental fixes on preexisting readers.

## Gotchas

Reusing a connection does not mean reusing a database snapshot. An open failure must remain uncertainty, not a missing task. Filtering must happen after stale display fields are cleared. A supplied replica is borrowed, not owned by enrichment.

## plugin?

Core changes: the optimization boundaries sit inside canonical origin/sidebar readers. No plugin hook or new external API is added.

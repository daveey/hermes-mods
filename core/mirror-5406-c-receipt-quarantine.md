# Task-local containment receipt quarantine and certified release

Mirror entries: 5284, 5285, 5286, 5287, 5288, 5289, 5290, 5291, 5301, 5302. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Deterministic containment evidence conflicts quarantine the affected task and dependency descendants without stalling unrelated sync rows or pushes. Holds prevent claim, pickup, lifecycle bypass and affected outgoing writes. Transient/unavailable/unproven evidence still refuses the entire page with the cursor unchanged; it is not converted into a permanent local hold.

Operators can list with `hermes kanban --board <board> sync quarantine --json`, then deliberately accept the Board's live evidence via `sync quarantine --release <receipt> --reason <text>`. Release is acceptance plus certified catch-up, not a bare hold deletion. Each unreleased receipt independently holds its task/descendants.

## How it works

Only typed identity-changed or event-differs conflicts enter normal quarantine. The conflicting wire row, per-receipt direct/dependency holds and withheld changes are durable before the feed cursor advances; local holds are mirrored before later-page failure and preserved through replica rebuild. Delete-owner bookkeeping keeps a held task's own chat-link/attachment deletes pending while unrelated deletes continue. Pending local edit fields remain authoritative until their Board answer. Filtering occurs before the outgoing batch limit so a held FIFO prefix cannot starve healthy rows.

Release acquires the board sync lock and handles replica first, then live. Evidence is prefetched outside the write transaction and frozen for certification. In each file one transaction appends immutable acceptance evidence/digest/reason/operator/time, certifies catch-up, applies current/version/HLC-fenced rows and lifts only that receipt. Failure rolls back acceptance and lift together. Withheld replay is ordered by withhold epoch then sequence, including snapshot rows. Freed protected events use canonical current evidence; another newly uncertifiable event receives its own hold and the selection is recomputed.

Primary live files replay their own partition. Non-primary live files adopt current certified replica rows for freed tasks in the same lift transaction, refuse if the replica still holds them, and discard only freed-task withheld rows; unknown subjects or tasks held by other receipts stay withheld. Replica/live first-seen stamps share one owner-notice key per host/receipt/quarantine episode. Notices use the courier with retry backoff; the hub route logs rather than filing to itself. Repeated read-only shadow peeks avoid extra pooled connections and fail closed on unreadability. Existing quarantine table shapes are checked exactly, not guessed/migrated.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_containment_connections.py`
- `hermes_cli/kanban_containment_guard.py`
- `hermes_cli/kanban_containment_lifecycle.py`
- `hermes_cli/kanban_containment_replica.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_pending.py`
- `hermes_cli/kanban_sync_quarantine.py`
- `hermes_cli/kanban_sync_quarantine_notice.py`
- `hermes_cli/kanban_sync_quarantine_release.py`
- `hermes_cli/kanban_sync_quarantine_release_live.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source suites cover changed receipts with healthy sibling rows, immutable cache/holds, claim/pickup/push refusal, later dependencies, cursor safety on journal failure, rebuild custody, FIFO fairness, mirroring before later transport failure, unrelated versus held deletes, typed conflict/transient distinctions, multiple receipt/dependency holds, and no-op adoption. CLI fixtures exercise listing/reason/unknown/busy/live-evidence refusal and successful release. Release tests cover live/version fences, transaction rollback leaving no acceptance, canonical catch-up, withhold-order delete/snapshot replay, newly quarantined events, primary versus non-primary live catch-up, retaining other-held/unidentifiable wires and clean refusal/partial-release reporting. Notice tests cover per-host/episode idempotency, shared replica/live notice keys and backoff; no live courier delivery is executed here.

Relevant source suites/fixtures:

- `tests/hermes_cli/kanban_quarantine_support.py`
- `tests/hermes_cli/test_kanban_containment_feed_predicates.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_cli.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_conflict.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_followups.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_holds.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_notice.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_release.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_review.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_round4.py`
- `tests/hermes_cli/test_kanban_receipt_quarantine_safety.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

The first observed receipt and acceptance audit are not rewritten. A local quarantine is not a containment episode; `resume-contained` cannot lift it. Replica and live releases are individually atomic, not one cross-file transaction: partial progress is reported with the remaining action. A downgrade to core unaware of these tables neither enforces holds nor replays cursor-passed withheld rows; rebuild/resync and inspect safety state before trusting such a downgrade. Unknown historical table shapes refuse for explicit recovery. CLI/apply tests are fixture-backed; live Board/courier, real operator adjudication and Desktop release entry paths are untested.

## Why core instead of a plugin

The quarantine crosses feed certification, SQL guards, cursor advancement, replica/live pickup, outgoing batches and release transactions. A plugin cannot safely skip a bad feed row while preserving these atomic safety obligations.

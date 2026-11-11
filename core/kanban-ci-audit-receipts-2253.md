# Durable CI audit rows in acceptance receipts

Patches: **2231–2232**.

## Behavior

Persisted PR acceptance events retain CI chronology and a canonical audit URL alongside the check name, SHA, classification and conclusion. Evidence can be inspected after the hosting service deletes its run data. The companion contribution-mapping change attributes automated CI-proof commits to the repository owner; it does not change runtime behavior.

## How it works

`check_receipt` adds `html_url` and `completed_at`. Check runs use their completion timestamp; legacy status checks use their creation timestamp. URL selection falls back from the run URL to the status target URL. The resulting rows remain in the card's `pr_acceptance` event payload.

## Code

- `hermes_cli/kanban_pr_merge_time.py`: receipt fields.
- `contributors/emails/`: companion worker-author attribution mapping (metadata only).

## Tests

Source coverage, **not run in this mirror**: `tests/hermes_cli/test_kanban_pr_merge_time.py::test_completed_landing_proof_survives_github_expiry` completes a card with PR evidence, removes upstream run results, forbids further API access and reads the stored receipt's check fields. Patch 2232 adds no executable test; its scope is contribution metadata.

## Applying

Use the [README full replay procedure](README.md#applying); the existing PR acceptance machinery supplies validation and event persistence.

## Gotchas

This preserves metadata, not downloadable CI logs or artifacts. Historical receipt rows are not backfilled. Persistence does not make a missing or failed CI check acceptable.

## Plugin

Core acceptance-receipt serialization plus contribution metadata; no plugin required.

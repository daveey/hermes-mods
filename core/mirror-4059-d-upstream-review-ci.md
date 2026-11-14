# Exact-head upstream review discovery and CI gate boundary tests

Mirror snapshot 4059; covered ordinals: 3915, 3926, 3933, 3939, 3950, 3951.

## Behavior

- Upstream sync can discover completed off-key review candidates bound to the exact PR URL and full lowercase head SHA when no keyed review card exists.
- Discovery is not approval: the authoritative review gate still checks custody, independence, current verdict, and existing GitHub restrictions. A keyed review takes precedence, and a terminal rejection from any candidate blocks other approvals.
- Test-only additions pin CI queue pagination/timeouts and conservative upstream merge classification; updater fixtures model the real trusted upstream identity.

## How it works

`scripts/ci/upstream_sync_review.py:off_key_review_cards` scans read-only board paths for done tasks with completed done runs and matching structured metadata, ignoring unreadable stores and malformed records and deduplicating IDs. Patch 3951 includes both approve and request-changes metadata. `ShipReview.check` tries only a keyed card when one exists; otherwise it inspects discovered candidates plus the normal no-card route. It accumulates receipts but returns immediately on `ReviewRejected`/`ReviewChangesRequested`, so iteration order cannot hide rejection behind approval. Reply guards still evaluate receipts before acceptance.

Updater fixture `FakeFork.adopt` configures the canonical public upstream URL with a local `insteadOf` mapping, keeping real identity validation/fetch logic while avoiding network dependence.

## Code

- `scripts/ci/upstream_sync_review.py`

### Mirrored commits

- 3915: `6236b65ec980f2d77bd4e5f0e2cac3a957881cb3` — Pins bounded gh reads and fail-open scheduled test selection.
- 3926: `1018afeefbc71c0838db46af404ac44ac9e31c24` — Models trusted upstream identity in local updater fixtures.
- 3933: `43553d02e215798816a514d7f020d402c5b9bd35` — Pins the final supported CI queue page before saturation decisions.
- 3939: `9f181892a63b06a59bca8cac5561bee302f46678` — Pins octopus and unparsed remerge output as review-required.
- 3950: `68043abc6e56f524bb30d72ab527e8d8ba2f79e9` — Discovers exact-head off-key review candidates without granting approval.
- 3951: `11922a98109e7b6e738dd5f65fa6deed6bfcd43c` — Preserves terminal rejection across every off-key review candidate.

## Tests

- `tests/ci/test_macos_os_tests_workflow.py`: a hung `gh api` read has a positive bounded timeout and fails open to running tests. The last supported runs page is read; a queued job there suppresses the scheduled slot, while empty/in-progress controls do not.
- `tests/hermes_cli/test_update_fork_merge_upstream.py`, `tests/hermes_cli/test_update_fork_sync_identity.py`, and helper `tests/hermes_cli/upstream_gate_fake.py`: fixtures adopt the canonical upstream identity while routing Git traffic locally; their existing integration assertions now traverse the real identity gate.
- `tests/scripts/ci/test_upstream_sync_review.py`: an octopus merge is fork-authored and parks for review even without new nonmerge commits; nonempty unparsed remerge output demands review rather than becoming a clean merge.
- `tests/scripts/ci/test_upstream_sync_offkey_review.py`: exact-head approved candidates can reach the authoritative gate without duplicate requests; wrong PR/SHA, abbreviated SHA, malformed IDs/metadata, unfinished tasks/runs, and wrong outcomes do not qualify. Replica duplication is deduplicated. Discovery alone cannot approve and preserves implementer identity; keyed refusals cannot be bypassed. Both terminal rejection types block approvals in either candidate order, without filing past rejection.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

Off-key metadata is candidate discovery only, never independent approval evidence. An unreadable replica is skipped, not trusted. Exact SHA matching is literal; abbreviated or differently cased tokens must not be repaired. The CI queue gate's fail-open-to-testing policy differs intentionally from fail-closed landing review.

## Plugin?

No. Review custody and CI gate behavior belong in the existing core release/review integration.

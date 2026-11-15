# Upstream review selection, approval guards and blob recovery

Mirror ordinals: 3965, 3977, 3978, 3990, 3991.

## Behavior

Selects subprocess-based updater integration consumers when gate files change, validates every approving receipt, and recovers refused promised blobs before merge classification without weakening refusal rules.

## How it works

The test selector seeds the copied fixture helper as a dependency root for scripts/ci/upstream_sync*.py, preserving transitive import closure and suite filtering even on deleted/renamed paths. Review reply guards run for every independently validated approving receipt before the first receipt is used for the success message. Remerge classification retries only recognized missing-object errors, recovers at most 32 distinct blobs through configured GitHub remotes, validates base64 and Git object identity before storing bytes, and refuses repeated/no-progress failures.

## Code

- `scripts/ci/affected_tests.py`
- `scripts/ci/upstream_sync_review.py`

## Tests

- `tests/ci/test_affected_tests.py`: Direct and indirect consumers join the selected suite for gate-family changes and renames; unrelated directories/extensions do not. Both real updater integration consumers are selected.
- `tests/scripts/ci/test_upstream_sync_review.py`: Real Git controls reject any nongenuine configured upstream URL, accept mixed-case genuine identity and refuse ship-tool import RuntimeError. Real partial-clone fixtures exercise classifier and standalone CLI recovery; unavailable/malformed/wrong-byte/timeout/hash/store failures refuse, repeated failures stop, recovery caps at 32 and binary bytes round-trip. The unparsed-output fixture now stubs the new _git seam and retains review-required behavior.
- `tests/scripts/ci/test_upstream_sync_offkey_review.py`: A second off-key approving receipt with behavioral findings blocks without answers and succeeds with a reply; both receipts are guarded, implementation identity is retained and no duplicate review is filed.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Blob recovery changes the local object store, not review requirements. A nonempty unparsed remerge diff still requires review. These tests use controlled remotes and do not prove a live hosting service response.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

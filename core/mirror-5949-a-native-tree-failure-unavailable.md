# Native project-tree capture failures stay unavailable

Patch coverage: **5934**.

## Behavior

If the native project-tree or project drill-in capture fails for a profile, the public read reports unavailability rather than a fresh empty tree or null project. A cold source is `unknown` (503, `facts_unavailable`); a warm source keeps its last-good value, observation time and source vector but is marked `stale`, with error code `board_capture_failed`. There is no legacy fallback and no raw exception in the native response. After the input recovers the view returns to fresh. The aggregate profiles tree keeps its partial results and per-profile error list unchanged.

## How it works

The shared profile fan-out reader collects per-profile failures into a list. In `fact_tree_inputs.capture`, when the selector is native and that list is non-empty, it raises `OSError`, which enters the owner's existing input-source failure path. The profiles surface still folds the list into its error envelope.

## Code

- `hermes_cli/fact_tree_inputs.py`: native-only raise after `_read_profile_db`.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live result is claimed.

`tests/hermes_cli/test_fact_tree_failures.py`: cold and warm failure for both tree and project-sessions views through the public read, 503 detail, retained stale evidence, recovery to fresh, the legacy builder's failure kept as a parity control, and the profiles partial-error envelope unchanged. Untested entry path: a full served HTTP request against a real failing store.

## Applying

Apply the covered patches only as part of the full ordered core series, following the whole segmented recipe in the [core README Applying section](README.md#applying) (replay bridges and required repair overlays included). The mirror is append-only and these entries depend on earlier ones; do not run a standalone `git am` on them and do not treat them as an independent cherry-pick set. A reimplementation on a newer upstream should keep the invariants above and run the named suites in a disposable source checkout.

## Gotchas

- The guard must be native-only; raising for the profiles surface would remove its partial-result contract.
- Do not catch the error between the reader and the source; that restores the false empty success.
- Depends on [the in-process board and tree owners](mirror-5949-a-native-board-tree-owners.md).

## Why not a plugin

No: it is a failure-propagation rule in the core fact input boundary shared with the profiles router.

# Import-aware affected-test planning and reusable selections

Mirror entries: 4803, 4811, 4849.

## Behavior

Pull-request CI selects affected Python files with bounded import propagation, uses smaller affected slices and reuses the detect job selection instead of recomputing it in each slice.

## How it works

Tests contribute every relevant import; source modules propagate through import-time imports and one function-level hop. TYPE_CHECKING-only imports are excluded. Full-suite defaults expand to twelve slices while the separate upstream-sync matrix remains explicitly eight. Affected slice budgets are reduced to 16 × 120 seconds of estimated file work; the matrix cap stays twelve and selections over the existing 40% cutoff use the full suite. Detect saves a sorted unique file list with an exact run/attempt Actions-cache key. Slice restore is bounded and best effort; unusable lists fall back to planning from changed paths. Any unparseable tracked Python file forces full mode rather than planning on a graph with missing edges.

## Code

- `.github/workflows/ci.yaml`
- `.github/workflows/tests.yml`
- `.github/workflows/upstream-sync.yml`
- `scripts/ci/affected_tests.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Affected-tests tests cover import-time closure, class/async scopes, one-hop limits, type-only imports, valid reuse without planning, unusable lists and parse failure. Fast-runner/detect routing tests cover matrix defaults, exact cache keys, best-effort cache steps and retained changed-path fallback.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/ci/test_affected_tests.py`
- `tests/ci/test_fast_runner_routing.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Two or more source-level function-import hops can miss a PR selection and rely on main-push coverage. Cache reuse is not required artifact storage and failure must not narrow selection. Estimated CI wall-time improvements are not measurements from this task.
